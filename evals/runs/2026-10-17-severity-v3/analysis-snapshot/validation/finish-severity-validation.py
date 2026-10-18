import ast
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

root = Path.cwd()
run = root / 'evals/runs/2026-10-17-severity-v3'
manifest = json.loads((run / 'manifest.json').read_text(encoding='utf-8'))
assert manifest['status'] == 'complete' and not (run / '.writer-lock').exists()
frozen = manifest['identity']['files']
changed = [f for f, h in frozen.items() if hashlib.sha256((root / f).read_bytes()).hexdigest() != h]
# Shared workspace changes are preserved. Inference used the already imported modules
# and the frozen rubric snapshot; verify actual requests separately after rendering.
assert changed in ([], ['app/models.py']), changed
for relative, expected in frozen.items():
    assert hashlib.sha256((run / 'source-snapshot' / relative).read_bytes()).hexdigest() == expected
if changed:
    before_tree = ast.parse((run / 'source-snapshot/app/models.py').read_text(encoding='utf-8'))
    current_tree = ast.parse((root / 'app/models.py').read_text(encoding='utf-8'))
    def judge_schema(tree):
        names = {'ERROR_TYPES', 'SEVERITIES', 'SEVERITY_WEIGHTS', 'LANG_PROFILES', 'AnnotationPayload', 'SubmitRequest'}
        return [ast.dump(n, include_attributes=False) for n in tree.body
                if getattr(n, 'name', None) in names or isinstance(n, ast.Assign) and any(getattr(t, 'id', None) in names for t in n.targets)]
    assert judge_schema(before_tree) == judge_schema(current_tree), 'judge schema changed'
    current_copy = run / 'analysis-snapshot/workspace_at_completion/app/models.py'
    current_copy.parent.mkdir(parents=True, exist_ok=True)
    current_copy.write_bytes((root / 'app/models.py').read_bytes())
drift = {'changed_live_paths': changed, 'frozen_snapshots_intact': True,
         'judge_constants_and_annotation_schema_ast_unchanged': True,
         'scope': 'Live app/models.py gained separate speech-review fields while inference was running. Preserve those edits. Running Python modules were already imported; all actual request payloads are checked against the frozen protocol.',
         'live_sha256': {f: hashlib.sha256((root / f).read_bytes()).hexdigest() for f in changed}}
(run / 'validation/workspace-drift.json').write_text(json.dumps(drift, indent=2) + '\n', encoding='utf-8')
parent = Path(tempfile.mkdtemp(prefix='severity-v3-recovery-', dir=root / 'runtime'))
copy = parent / 'run'
shutil.copytree(run, copy)
checkout = parent / 'frozen-checkout'
for relative in set(frozen) | set(manifest['identity']['prior_artifact_hashes']):
    source = run / 'source-snapshot' / relative if relative in frozen else root / relative
    target = checkout / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
# judge.py imports the mock-only LF map even for real-model evaluation. This
# unchanged repository dependency was not in SOURCE_FILES; record support separately.
support_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
support = subprocess.check_output(['git', 'show', support_commit + ':app/lf.py'], cwd=root)
assert support.decode('utf-8').replace('\r\n', '\n') == (root / 'app/lf.py').read_text(encoding='utf-8')
(checkout / 'app/lf.py').write_bytes(support)
support_copy = run / 'analysis-snapshot/recovery-support/app/lf.py'
support_copy.parent.mkdir(parents=True, exist_ok=True)
support_copy.write_bytes(support)
support_record = {'path': 'app/lf.py', 'git_commit': support_commit,
                  'sha256': hashlib.sha256(support).hexdigest(),
                  'scope': 'Unchanged Git dependency for judge.py import; LF_TO_ERROR is used by MockJudge, not the real OpenAIJudge path. Not retrospectively added to the frozen input manifest.'}
metadata_path = checkout / 'evals/provenance/890m-qwen-v3.json'
metadata_path.parent.mkdir(parents=True, exist_ok=True)
metadata_path.write_bytes((root / 'evals/provenance/890m-qwen-v3.json').read_bytes())
raw = copy / 'attempts.jsonl'
before = raw.read_bytes()
args = [sys.executable, 'scripts/run_severity_study.py', '--metadata', 'evals/provenance/890m-qwen-v3.json', '--out', str(copy), '--resume']
valid = subprocess.run(args, capture_output=True, text=True, cwd=checkout)
assert valid.returncode == 0, valid.stderr
assert raw.read_bytes() == before
lock = copy / '.writer-lock'
lock.write_text('validation-owned dummy lock', encoding='utf-8')
locked = subprocess.run(args, capture_output=True, text=True, cwd=checkout)
assert locked.returncode == 2 and 'writer lock' in locked.stderr
lock.unlink()
metadata = json.loads((root / 'evals/provenance/890m-qwen-v3.json').read_text())
metadata['negative_identity_probe'] = True
changed_meta = parent / 'changed-metadata.json'
changed_meta.write_text(json.dumps(metadata), encoding='utf-8')
bad_args = args.copy()
bad_args[bad_args.index('--metadata') + 1] = str(changed_meta)
identity = subprocess.run(bad_args, capture_output=True, text=True, cwd=checkout)
assert identity.returncode == 2 and 'resume identity changed' in identity.stderr
assert raw.read_bytes() == before
lines = before.decode('utf-8').splitlines(keepends=True)
lines[0], lines[1] = lines[1], lines[0]
raw.write_text(''.join(lines), encoding='utf-8')
corrupt_before = raw.read_bytes()
order = subprocess.run(args, capture_output=True, text=True, cwd=checkout)
assert order.returncode == 2 and 'saved attempts must be' in order.stderr
assert raw.read_bytes() == corrupt_before
recovery = {'import_support': support_record, 'completed_resume_exit': valid.returncode, 'original_attempt_count': len(lines),
            'completed_resume_raw_unchanged': True, 'changed_identity_rejected': True,
            'concurrent_writer_rejected': True, 'corrupt_order_rejected': True,
            'scope': 'All recovery probes use an ignored run copy plus reconstructed frozen checkout; live workspace and original evidence untouched; no inference replay.'}
(run / 'validation/recovery-check.json').write_text(json.dumps(recovery, indent=2) + '\n', encoding='utf-8')
suites = list(ElementTree.parse(run / 'validation/severity-v3-tests.xml').getroot().iter('testsuite'))
counts = {field: sum(int(s.attrib.get(field, 0)) for s in suites) for field in ('tests', 'failures', 'errors', 'skipped')}
assert counts == {'tests': 173, 'failures': 0, 'errors': 0, 'skipped': 0}
packet = root / 'runtime/severity-v3-review/reviewer-packet'
review = [json.loads(s) for s in (packet / 'inputs.jsonl').read_text(encoding='utf-8').splitlines()]
assert len(review) == len({e['id'] for e in review}) == 28
assert all(set(e) == {'id', 'source', 'hypothesis'} for e in review)
assert (packet / 'rubric.md').read_bytes() == (root / 'data/guidelines/severity-v3.md').read_bytes()
for f, h in manifest['identity']['prior_artifact_hashes'].items():
    assert hashlib.sha256((root / f).read_bytes()).hexdigest() == h, f
result = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
          'current_workspace_windows_tests': json.loads((run / 'validation/current-workspace-tests.json').read_text(encoding='utf-8')),
          'pre_inference_windows_tests': counts, 'test_log': 'validation/severity-v3-tests.log',
          'test_junit': 'validation/severity-v3-tests.xml', 'frozen_snapshots_unchanged_at_completion': True, 'live_workspace_drift': drift,
          'prior_artifact_hashes_verified': len(manifest['identity']['prior_artifact_hashes']),
          'recovery': recovery, 'reviewer_packet': {'items': 28, 'opaque_ids': True, 'gold': False,
              'model_outputs': False, 'split_indicators': False, 'rubric_bytes_match': True, 'human_review_completed': False},
          'timing_scope': 'Interactive desktop; user confirmed concurrent computation (validation/user-load-context.json); workload type and start/end unrecorded. Substantial later-pass latency drift. Single post-drift CPU/RAM observation is not a time series or causal attribution. Keep all requests; no performance-isolation claim.',
          'not_executed': ['H100 experiment', 'AWS deployment/load/failover', 'Docker smoke', 'Linux CI', 'ARM64 CI']}
(run / 'validation.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
script_copy = run / 'analysis-snapshot/validation/finish-severity-validation.py'
script_copy.parent.mkdir(parents=True, exist_ok=True)
script_copy.write_bytes(Path(__file__).read_bytes())
print(json.dumps({'tests': counts, 'recovery': recovery, 'prior_hashes': result['prior_artifact_hashes_verified']}))
