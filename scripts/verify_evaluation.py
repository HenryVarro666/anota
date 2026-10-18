"""Verify a committed evaluation's hashes and recompute metrics without a model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.evaluation import summarize


def verify(run):
    hashes = json.loads((run / "artifact-sha256.json").read_text(encoding="utf-8"))
    for relative, expected in hashes.items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"artifact hash mismatch: {relative}")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    identity = manifest["identity"]
    for relative, field in {"app/judge.py": "judge_code_sha256", "app/evaluation.py": "metrics_code_sha256",
                            "scripts/evaluate_judge.py": "runner_code_sha256", "data/guideline.md": "guideline_sha256"}.items():
        if hashlib.sha256((run / "source-snapshot" / relative).read_bytes()).hexdigest() != identity[field]:
            raise ValueError(f"executed source hash mismatch: {relative}")
    dataset = ROOT / "evals/data/translation_probe_v1.jsonl"
    if hashlib.sha256(dataset.read_bytes()).hexdigest() != identity["dataset_sha256"]:
        raise ValueError("dataset hash mismatch")
    examples = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    records = [json.loads(line) for line in (run / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    if len({r["key"] for r in records}) != len(records):
        raise ValueError("duplicate attempt keys")
    summary = summarize(records, examples, identity["profile"]["execution"]["repeats"])
    if summary != json.loads((run / "summary.json").read_text(encoding="utf-8")) or not summary["complete"]:
        raise ValueError("incomplete or inconsistent metrics")
    print(f"Verified {len(hashes)} artifact hashes, executed source identity and {summary['completed_attempts']} timed attempts.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    verify(Path(parser.parse_args().run).resolve())
