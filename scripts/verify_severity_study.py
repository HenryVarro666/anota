"""Verify v3 integrity, request contracts, disputed quarantine and recomputed scores."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.judge import SYSTEM_TEMPLATE, VERDICT_SCHEMA
from app.models import ERROR_TYPES, SEVERITIES
from app.severity_study import severity_summary
from scripts.run_severity_study import WARMUP, read_rows


def verify(run):
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    identity = manifest["identity"]
    spec = identity["spec"]
    hashes = json.loads((run / "artifact-sha256.json").read_text(encoding="utf-8"))
    for relative, expected in hashes.items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("artifact mismatch: " + relative)
    for relative, expected in identity["prior_artifact_hashes"].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("historical evidence mismatch: " + relative)
    snapshot = run / "source-snapshot"
    for relative, expected in identity["files"].items():
        if hashlib.sha256((snapshot / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("frozen source mismatch: " + relative)
    primary, disputed, regression = [read_rows(snapshot / spec[k]) for k in ("holdout", "disputed", "regression")]
    if any("gold" in row for row in disputed):
        raise ValueError("disputed set contains single gold labels")
    examples = {e["id"]: e for e in primary + disputed + regression + [WARMUP]}
    rows = read_rows(run / "attempts.jsonl")
    if [r["key"] for r in rows] != identity["order"]:
        raise ValueError("observed calls do not match the full frozen order")
    for row in rows:
        arm = row["arm"]
        example, profile = examples[row["task_id"]], identity["profiles"][arm]
        guideline = (snapshot / spec["arms"][arm]["guideline"]).read_text(encoding="utf-8")
        settings = profile["request"]
        user = f"SOURCE:\n{example['source']}\n\nHYPOTHESIS:\n{example['hypothesis']}\n\n{settings['prompt_suffix']}"
        expected = {"model": profile["model"], "temperature": 0.0, "max_tokens": settings["max_tokens"],
                    "messages": [{"role": "system", "content": SYSTEM_TEMPLATE.format(guideline=guideline, etypes=ERROR_TYPES, sevs=SEVERITIES)},
                                 {"role": "user", "content": user}]}
        if settings["structured_output"]:
            expected["response_format"] = {"type": "json_schema", "json_schema": {"name": "translation_verdict", "strict": True, "schema": VERDICT_SCHEMA}}
        if row["trace"]["request"] != expected:
            raise ValueError("request differs from frozen contract: " + row["key"])
    actual = severity_summary(rows, primary, disputed, regression, spec["gates"])
    if actual != json.loads((run / "summary.json").read_text(encoding="utf-8")) or not actual["complete"] or manifest["status"] != "complete":
        raise ValueError("summary mismatch or incomplete study")
    print(f"Verified {len(hashes)} new artifact hashes, {len(identity['prior_artifact_hashes'])} historical hashes, {len(rows)} complete request contracts and all scores. Disputed cases remain separate.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    verify(Path(parser.parse_args().run).resolve())
