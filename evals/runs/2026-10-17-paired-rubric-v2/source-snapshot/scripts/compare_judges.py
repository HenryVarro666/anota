"""Run a frozen, sequential paired baseline/candidate prompt comparison."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.comparison import comparison_summary
from app.judge import JudgeUnavailable, OpenAIJudge


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def validate_fixtures(holdout, development):
    if not holdout or len({e["id"] for e in holdout}) != len(holdout):
        raise ValueError("holdout ids must be unique and nonempty")
    if len({e["parent_id"] for e in holdout}) != len(holdout):
        raise ValueError("each holdout item needs a unique source parent")
    if {e["source"].strip().casefold() for e in holdout} & {e["source"].strip().casefold() for e in development}:
        raise ValueError("holdout overlaps a development source")
    if {e["parent_id"] for e in holdout} & {e["parent_id"] for e in development}:
        raise ValueError("holdout overlaps a development parent")


def schedule_requests(holdout, development):
    warmup = {"id": "warmup", "source": "The reception desk is near the elevator.",
              "hypothesis": "La recepción está cerca del ascensor."}
    schedule = [("warmup", 0, arm, warmup) for arm in ("baseline", "candidate")]
    for repeat in (1, 2):
        items = holdout.copy()
        random.Random(20261017 + repeat).shuffle(items)
        for example in items:
            # Alternate first arm by stable item index; reverse it on the second pass.
            original_index = next(i for i, row in enumerate(holdout) if row["id"] == example["id"])
            arms = ("baseline", "candidate") if (original_index + repeat) % 2 else ("candidate", "baseline")
            schedule.extend(("holdout", repeat, arm, example) for arm in arms)
    schedule.extend(("development", 1, "candidate", example) for example in development)
    return schedule


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    source_files = ["evals/data/holdout_v2.jsonl", "evals/data/translation_probe_v1.jsonl",
                    "evals/profiles/local-lmstudio.json", "evals/profiles/local-lmstudio-v2.json",
                    "data/guideline.md", "app/judge.py", "app/evaluation.py", "app/comparison.py",
                    "scripts/compare_judges.py", "scripts/build_comparison_fixture.py",
                    "evals/reviews/pilot-v1-audit.md", "tests/test_comparison.py", "constraints-runtime.txt"]
    profiles = {arm: json.loads((ROOT / path).read_text(encoding="utf-8")) for arm, path in [
        ("baseline", "evals/profiles/local-lmstudio.json"), ("candidate", "evals/profiles/local-lmstudio-v2.json")]}
    # Isolate the suffix: endpoint, weights, timeout, schema, output budget and execution are equal.
    b, c = profiles["baseline"], profiles["candidate"]
    if any(b[key] != c[key] for key in ("base_url", "model", "execution", "load_contract", "model_artifact")):
        parser.error("candidate changed more than the prompt contract")
    if {k:v for k,v in b["request"].items() if k != "prompt_suffix"} != {k:v for k,v in c["request"].items() if k != "prompt_suffix"}:
        parser.error("request settings differ beyond prompt suffix")
    holdout = read_rows(ROOT / source_files[0])
    development = read_rows(ROOT / source_files[1])
    validate_fixtures(holdout, development)
    schedule = schedule_requests(holdout, development)
    identity = {"files": {p: digest(ROOT / p) for p in source_files}, "profiles": profiles,
                "provenance": json.loads(Path(args.metadata).read_text(encoding="utf-8-sig")),
                "order": [f"{phase}:{repeat}:{arm}:{e['id']}" for phase, repeat, arm, e in schedule],
                "gates": ["validity, binary agreement, critical recall no worse on each pass",
                          "clean false alarms no worse on each pass", "candidate P95 <= 1.5 * baseline P95 on each pass"],
                "independence": "24 novel synthetic parents; same agent wrote provisional labels and candidate before inference; no independent expert adjudication"}
    out = Path(args.out)
    if args.resume:
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        if manifest["identity"] != identity:
            parser.error("resume identity changed; use a new run")
    else:
        out.mkdir(parents=True, exist_ok=False)
        manifest = {"identity": identity, "started_at_utc": datetime.now(timezone.utc).isoformat(),
                    "status": "running", "python": platform.python_version(), "platform": platform.platform(),
                    "automatic_retries": 0, "warmups": 2, "temperature": 0,
                    "environment": "Windows desktop; no other generation load; power/thermal/host isolation not instrumented"}
        for relative in source_files:
            snapshot = out / "source-snapshot" / relative
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes((ROOT / relative).read_bytes())
        write_json(out / "manifest.json", manifest)  # Written BEFORE the first request.
    raw_path = out / "attempts.jsonl"
    records = read_rows(raw_path) if raw_path.exists() else []
    completed = {r["key"] for r in records}
    if len(completed) != len(records):
        parser.error("duplicate attempt keys in saved run")
    traces = []
    judges = {}
    for arm, profile in profiles.items():
        request = profile["request"]
        judges[arm] = OpenAIJudge(profile["base_url"], model=profile["model"],
            api_key=os.environ.get("ANOTA_JUDGE_API_KEY", "EMPTY"),
            guideline_text=(ROOT / "data/guideline.md").read_text(encoding="utf-8"),
            timeout=request["timeout_seconds"], max_tokens=request["max_tokens"],
            structured_output=request["structured_output"], prompt_suffix=request["prompt_suffix"], observer=traces.append)
    failures = 0
    with raw_path.open("a", encoding="utf-8") as handle:
        for phase, repeat, arm, example in schedule:
            key = f"{phase}:{repeat}:{arm}:{example['id']}"
            if key in completed:
                continue
            traces.clear()
            verdict, error = None, None
            try:
                verdict = judges[arm].evaluate(example, [])
            except JudgeUnavailable as exc:
                error = str(exc)
            record = {"key": key, "phase": phase, "repeat": repeat, "arm": arm,
                      "task_id": example["id"], "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                      "verdict": verdict, "error": error, "trace": traces[-1]}
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            records.append(record)
            write_json(out / "summary.json", comparison_summary(records, holdout, development))
            print(f"{len(records)}/{len(schedule)} {key}: {'valid' if verdict else 'FAILED'} {traces[-1]['elapsed_seconds']:.2f}s", flush=True)
            failures = failures + 1 if error else 0
            if failures >= 3:
                manifest["status"] = "stopped_after_three_failures"
                write_json(out / "manifest.json", manifest)
                raise SystemExit("Stopped; raw failures preserved.")
    manifest.update(status="complete", finished_at_utc=datetime.now(timezone.utc).isoformat())
    write_json(out / "manifest.json", manifest)


if __name__ == "__main__":
    main()
