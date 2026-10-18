"""Verify the complete paired experiment and rederive metrics without a model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.comparison import comparison_summary


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify(run):
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    identity = manifest["identity"]
    hashes = json.loads((run / "artifact-sha256.json").read_text(encoding="utf-8"))
    for relative, expected in hashes.items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("artifact mismatch: " + relative)
    for relative, expected in identity["files"].items():
        if hashlib.sha256((run / "source-snapshot" / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("frozen input mismatch: " + relative)
    holdout = read_rows(run / "source-snapshot/evals/data/holdout_v2.jsonl")
    development = read_rows(run / "source-snapshot/evals/data/translation_probe_v1.jsonl")
    by_id = {e["id"]: e for e in holdout + development}
    rows = read_rows(run / "attempts.jsonl")
    if [r["key"] for r in rows] != identity["order"]:
        raise ValueError("observed calls differ from the frozen order")
    for row in rows:
        if row["phase"] == "warmup": continue
        example = by_id[row["task_id"]]
        profile = identity["profiles"][row["arm"]]
        expected = f"SOURCE:\n{example['source']}\n\nHYPOTHESIS:\n{example['hypothesis']}\n\n{profile['request']['prompt_suffix']}"
        request = row["trace"]["request"]
        if request["messages"][1]["content"] != expected or request["model"] != profile["model"]:
            raise ValueError("observed prompt/model differs from its frozen contract")
    actual = comparison_summary(rows, holdout, development)
    saved = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    if actual != saved or not actual["complete"] or manifest["status"] != "complete":
        raise ValueError("metrics mismatch or incomplete run")
    print(f"Verified {len(hashes)} hashes, {len(rows)} ordered calls, no gold in user messages, and all paired metrics.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    verify(Path(parser.parse_args().run).resolve())
