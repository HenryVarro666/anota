"""Run a resumable evaluation through Anota's actual judge adapter."""
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
from app.evaluation import summarize
from app.judge import JudgeUnavailable, OpenAIJudge


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--out", required=True, help="new result directory, or same directory with --resume")
    parser.add_argument("--dataset", default=str(ROOT / "evals/data/translation_probe_v1.jsonl"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--metadata", help="JSON with model hash, runtime and hardware provenance")
    args = parser.parse_args()
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8-sig"))
    dataset = Path(args.dataset)
    examples = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({row["id"] for row in examples}) != len(examples):
        parser.error("dataset contains duplicate ids")
    base_url = os.environ.get(profile.get("base_url_env", ""), profile.get("base_url"))
    model = os.environ.get(profile.get("model_env", ""), profile.get("model"))
    if not base_url or not model:
        parser.error("set the endpoint and model environment variables specified by the profile")
    if profile["execution"]["concurrency"] != 1:
        parser.error("this sequential pilot runner supports concurrency=1 only")
    request = profile["request"]
    repeats = profile["execution"]["repeats"]
    if not isinstance(repeats, int) or not 1 <= repeats <= 10:
        parser.error("repeats must be 1 through 10")
    output = Path(args.out)
    if output.exists() and not args.resume:
        parser.error("output exists; use a new directory or --resume")
    output.mkdir(parents=True, exist_ok=True)
    provenance = json.loads(Path(args.metadata).read_text(encoding="utf-8-sig")) if args.metadata else {}
    identity = {"dataset_sha256": digest(dataset), "profile": profile, "model": model,
                "endpoint": base_url, "guideline_sha256": digest(ROOT / "data/guideline.md"),
                "judge_code_sha256": digest(ROOT / "app/judge.py"),
                "runner_code_sha256": digest(Path(__file__)), "metrics_code_sha256": digest(ROOT / "app/evaluation.py"),
                "provenance": provenance}
    manifest_path = output / "manifest.json"
    if args.resume:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["identity"] != identity:
            parser.error("resume identity changed; use a new result directory")
    else:
        manifest = {"identity": identity, "started_at_utc": datetime.now(timezone.utc).isoformat(),
                    "python": platform.python_version(), "platform": platform.platform(),
                    "automatic_retries": 0, "warmups": 1, "status": "running"}
        write_json(manifest_path, manifest)
    raw_path = output / "attempts.jsonl"
    records = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()] if raw_path.exists() else []
    completed = {row["key"] for row in records}
    traces = []
    judge = OpenAIJudge(base_url, model=model, api_key=os.environ.get("ANOTA_JUDGE_API_KEY", "EMPTY"),
                        guideline_text=(ROOT / "data/guideline.md").read_text(encoding="utf-8"),
                        timeout=request["timeout_seconds"], max_tokens=request["max_tokens"],
                        structured_output=request["structured_output"], prompt_suffix=request["prompt_suffix"],
                        observer=traces.append)
    warmup = {"id": "warmup", "source": "The appointment starts at nine.",
              "hypothesis": "La cita comienza a las nueve."}
    schedule = [("warmup", 0, warmup)]
    for repeat in range(1, repeats + 1):
        items = examples.copy()
        random.Random(profile["execution"]["order_seed"] + repeat).shuffle(items)
        schedule.extend(("timed", repeat, row) for row in items)
    consecutive_failures = 0
    with raw_path.open("a", encoding="utf-8") as handle:
        for phase, repeat, example in schedule:
            key = f"{phase}:{repeat}:{example['id']}"
            if key in completed:
                continue
            traces.clear()
            verdict, error = None, None
            try:
                verdict = judge.evaluate(example, [])
            except JudgeUnavailable as exc:
                error = str(exc)
            record = {"key": key, "phase": phase, "repeat": repeat, "task_id": example["id"],
                      "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                      "verdict": verdict, "error": error, "trace": traces[-1]}
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            records.append(record)
            write_json(output / "summary.json", summarize(records, examples, repeats))
            print(f"{key}: {'valid' if verdict else 'unavailable'} {record['trace']['elapsed_seconds']:.2f}s", flush=True)
            consecutive_failures = consecutive_failures + 1 if error else 0
            if consecutive_failures >= 3:
                manifest["status"] = "stopped_after_three_consecutive_failures"
                write_json(manifest_path, manifest)
                raise SystemExit("Stopped after three failures; raw attempts preserved.")
    manifest["status"] = "complete"
    manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(manifest_path, manifest)
    print(json.dumps(summarize(records, examples, repeats), indent=2))


if __name__ == "__main__":
    main()
