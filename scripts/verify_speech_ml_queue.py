"""Verify real speech queues without reading gold or invoking ASR.

Uses immutable ASR/QC inputs, a temporary database and HTTP TestClient. All
submissions are automated lifecycle probes, never human annotation evidence.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient

from app.main import create_app
from app.speech import import_speech_manifest
from scripts.package_speechrank_models import package
from speechrank.runtime import DEFAULT_BUNDLE

SOURCES = (
    ('confidence', 'runtime/librispeech-dev-ml-v2-r1/asr.jsonl',
     'evals/runs/2026-10-17-librispeech-ml-ranking-v2/outputs/dev-qc/qc-reports.jsonl.gz'),
    ('legacy', 'runtime/librispeech-base-2026-10-17/test-clean.completed.asr.jsonl',
     'evals/runs/2026-10-17-librispeech-qc-ranking-v1/cohorts/full/qc-reports.jsonl.gz'),
)
SOURCE_FILES = (
    'app/main.py', 'app/tasks.py', 'app/speech.py', 'app/speech_queue.py',
    'speechrank/runtime.py', 'speechrank/features.py', 'speechrank/models.py',
    'scripts/package_speechrank_models.py', 'scripts/verify_speech_ml_queue.py',
    'static/app.js', 'static/speech.js', 'static/review.js', 'static/index.html', 'static/style.css',
)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def samples(count):
    rows, records = [], []
    for arm, asr, qc in SOURCES:
        selected = []
        with (ROOT / asr).open(encoding='utf-8-sig') as stream:
            for line in stream:
                row = json.loads(line)
                if row.get('status') == 'succeeded':
                    selected.append(row)
                if len(selected) == count:
                    break
        wanted = {row['item_id'] for row in selected}
        reports = {}
        with gzip.open(ROOT / qc, 'rt', encoding='utf-8') as stream:
            for line in stream:
                entry = json.loads(line)
                if entry['item_id'] in wanted:
                    reports[entry['item_id']] = entry['report']
        assert len(reports) == count
        for row in selected:
            row['audio_analysis'] = reports[row['item_id']]
            present = any(word.get('probability') is not None for word in row.get('words', []))
            assert present == (arm == 'confidence')
        rows.extend(selected)
        records.append({'arm': arm, 'asr': asr, 'qc': qc, 'count': count,
                        'asr_sha256': file_sha256(ROOT / asr),
                        'qc_sha256': file_sha256(ROOT / qc),
                        'item_ids': [row['item_id'] for row in selected]})
    return rows, records


def verify(work, count, preview_db=None):
    rows, inputs = samples(count)
    manifest = work / 'samples.jsonl'
    manifest.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
    bundle = work / 'bundle'
    package(DEFAULT_BUNDLE, bundle)
    previous = os.environ.get('ANOTA_SPEECHRANK_BUNDLE')
    os.environ['ANOTA_SPEECHRANK_BUNDLE'] = str(bundle)
    try:
        db_path = work / 'verification.db'
        app = create_app(db_path=str(db_path), export_dir=str(work / 'exports'))
        db, client = app.state.db, TestClient(app)
        imported = import_speech_manifest(db, manifest, 'real-hinted', show_suggestions=True)
        assert imported['n'] == 2 * count and imported['invalid'] == 0, imported
        bid = imported['batch_id']
        assert import_speech_manifest(db, manifest, 'real-hinted')['skipped'] == 2 * count
        clean = import_speech_manifest(db, manifest, 'real-clean', show_suggestions=False)['batch_id']

        def queue(batch=bid, sort='ml', annotator='automated-probe'):
            response = client.get('/api/speech/review/queue', params={
                'annotator': annotator, 'batch_id': batch, 'sort': sort})
            assert response.status_code == 200, response.text
            return response.json()

        queues = {sort: queue(sort=sort) for sort in ('ml', 'qc', 'fifo')}
        ml = queues['ml']
        assert ml['counts']['ml_confidence'] == count, ml['counts']
        assert ml['counts']['ml_legacy'] == count, ml['counts']
        densities = [item['ranking']['density'] for item in ml['items']]
        assert densities == sorted(densities, reverse=True)
        assert all(item['ranking']['method'] == 'qc' for item in queues['qc']['items'])
        assert all(item['ranking']['method'] == 'fifo' for item in queues['fifo']['items'])
        assert all('ranking' not in item for item in queue(clean)['items'])

        def claim(batch, task_id=None):
            response = client.post('/api/claim', json={'annotator': 'automated-probe',
                'batch_id': batch, 'speech_sort': 'ml', 'task_id': task_id})
            assert response.status_code == 200, response.text
            return response.json()['task']

        first = claim(bid)
        assert first['task_id'] == ml['items'][0]['task_id']
        assert client.post('/api/claim', json={'annotator': 'other-probe', 'batch_id': bid,
            'task_id': first['task_id']}).status_code == 409
        submitted = client.post('/api/speech/submit', json={
            'annotator': 'automated-probe', 'assignment_id': first['assignment_id'],
            'corrected_transcript': first['speech']['asr_text'],
            'note': 'Automated lifecycle verification; not human review.',
            'elapsed_ms': 1, 'active_ms': 0})
        assert submitted.status_code == 200, submitted.text
        undone = client.post('/api/undo', json={'annotator': 'automated-probe'}).json()['task']
        blind = client.post('/api/claim', json={'annotator': 'blind-probe', 'batch_id': clean}).json()['task']
        for task in (first, undone, blind):
            assert all(set(word) == {'start', 'end', 'w'} for word in task['speech']['words'])
            assert all(set(segment) == {'start', 'end', 'text'} for segment in task['speech']['segments'])
        assert 'ranking' not in blind and 'audio_analysis' not in blind['speech']
        exported = client.post('/api/export', json={'batch_id': bid}).json()
        snapshot = client.get(exported['download_url']).json()
        originals = {row['item_id']: row for row in rows}
        for item in snapshot['items']:
            provenance = item['task']['provenance']
            original = originals[provenance['item_id']]
            assert provenance['words'] == original['words']
            assert provenance['segments'] == original['segments']
            assert provenance['audio_analysis'] == original['audio_analysis']

        trans = db.execute("INSERT INTO batches(name,show_suggestions) VALUES('translation-check',0)")
        db.execute("INSERT INTO tasks(id,batch_id,source,hypothesis) VALUES('translation-check',?,'source','hypothesis')", (trans,))
        translation = client.post('/api/claim', json={'annotator': 'translation-probe', 'batch_id': trans}).json()['task']
        assert translation['task_id'] == 'translation-check' and 'speech' not in translation
        assert 'ranking' not in translation and 'suggestions' not in translation

        os.environ['ANOTA_SPEECHRANK_BUNDLE'] = str(work / 'missing-bundle')
        missing = queue(annotator='fallback-probe')
        assert missing['counts']['qc'] == 2 * count - 1
        assert all(item['ranking']['reason'] == 'bundle_missing' for item in missing['items'])
        os.environ['ANOTA_SPEECHRANK_BUNDLE'] = str(bundle)
        model = bundle / 'model-confidence.joblib'
        model.write_bytes(model.read_bytes() + b'tampered')
        tampered = queue(annotator='fallback-probe')
        assert tampered['counts']['qc'] == 2 * count - 1
        assert all(item['ranking']['reason'] == 'artifact_hash_mismatch' for item in tampered['items'])
        db.conn.close()
        os.environ['ANOTA_SPEECHRANK_BUNDLE'] = str(DEFAULT_BUNDLE)
        restarted = create_app(db_path=str(db_path), export_dir=str(work / 'exports'))
        client = TestClient(restarted)
        assert queue(annotator='restart-probe')['total'] == 2 * count - 1
        restored = client.post('/api/claim', json={'annotator': 'automated-probe', 'batch_id': bid}).json()['task']
        assert restored['assignment_id'] == first['assignment_id']
        restarted.state.db.conn.close()
        if preview_db is not None:
            import shutil
            preview_db.parent.mkdir(parents=True, exist_ok=True)
            if preview_db.exists():
                raise FileExistsError(preview_db)
            shutil.copyfile(db_path, preview_db)
        return {'status': 'passed', 'inputs': inputs, 'imported': imported['n'],
                'source_sha256': {name: file_sha256(ROOT / name)
                                  for name in SOURCE_FILES if (ROOT / name).is_file()},
                'new_asr_calls': 0, 'human_labels': 0, 'automated_lifecycle_submissions': 1,
                'gold_read': False, 'provenance_confidence_qc': 'exactly preserved',
                'checks': ['ML confidence and legacy arms', 'ML density descending',
                           'QC and FIFO sorting', 'claim agrees with queue', 'target lease conflict',
                           'submit undo export restart', 'raw confidence never exposed',
                           'clean ranking hidden', 'translation unchanged',
                           'missing bundle QC fallback', 'tampered bundle QC fallback'],
                'queues': queues, 'missing_bundle': missing, 'tampered_bundle': tampered}
    finally:
        if 'db' in locals():
            db.conn.close()
        if previous is None:
            os.environ.pop('ANOTA_SPEECHRANK_BUNDLE', None)
        else:
            os.environ['ANOTA_SPEECHRANK_BUNDLE'] = previous


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count-per-arm', type=int, default=10)
    parser.add_argument('--report', type=Path, default=ROOT / 'runtime/speech-ml-queue-check/verification.json')
    parser.add_argument('--preview-db', type=Path, help='Optional new persistent database for UI inspection')
    parser.add_argument('--review-dir', type=Path,
                        default=ROOT / 'evals/reviews/2026-10-17-anota-ml-queue-v1')
    args = parser.parse_args()
    assert args.count_per_arm > 0
    with tempfile.TemporaryDirectory(prefix='anota-real-queue-') as directory:
        result = verify(Path(directory), args.count_per_arm, args.preview_db)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    args.review_dir.mkdir(parents=True, exist_ok=True)
    (args.review_dir / 'verification.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.review_dir / 'README.md').write_text(
        '# Anota speech ML queue verification\n\n'
        'Real persisted ASR samples: 10 confidence dev rows and 10 legacy test rows '
        '(or the configured count per arm), merged with frozen QC by item ID. '
        'No gold/reference transcript files were opened and no ASR was run. '
        'One explicitly marked automated lifecycle submission is not a human label.\n\n'
        'The [verification report](verification.json) records input file SHA256, '
        'sample IDs, current application/runtime/JavaScript source SHA256, and '
        'actual HTTP queue results. It checks both ML arms, sorting, claim/lease, '
        'submit/undo/export/restart, confidence privacy, clean batches, translation, '
        'and missing/tampered model QC fallback. Confidence and QC export exactly.\n\n'
        'Reproduce: `.venv/Scripts/python.exe scripts/verify_speech_ml_queue.py`. '
        'Verification uses a temporary database and never writes frozen study inputs.\n\n'
        'Local-only runtime report: `runtime/speech-ml-queue-check/verification.json`; '
        'optional UI preview database: `runtime/speech-ml-queue-check/preview.db`. '
        'These ignored files are not GitHub downloads. The preview database '
        'contains automated probes only.\n', encoding='utf-8')
    with (args.review_dir / 'README.md').open('a', encoding='utf-8') as stream:
        stream.write('\nBrowser verification: actual ML/QC switching, clean evidence hiding, '
                     'Resume restoring the current waveform, and Play selection starting '
                     'audio playback all passed automated UI checks; no human labels were '
                     'created. [Queue screenshot](queue.png).\n')
    print(json.dumps({key: value for key, value in result.items() if key not in
                      ('inputs', 'source_sha256', 'queues', 'missing_bundle', 'tampered_bundle')}, indent=2))
    print('Report:', args.report)
