"""Execute a versioned severity study with exact-input resume and raw evidence."""
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
from app.judge import JudgeUnavailable, OpenAIJudge
from app.severity_study import severity_summary, validate_sets

SOURCE_FILES = ["app/judge.py", "app/models.py", "app/quality.py", "app/evaluation.py",
                "app/comparison.py", "app/severity_study.py", "scripts/run_severity_study.py",
                "scripts/build_severity_fixture.py", "tests/test_severity_study.py",
                "evals/severity-v3-protocol.md", "evals/reviews/disputed-labels-v3.md", "constraints-runtime.txt"]
WARMUP = {"id": "warmup", "source": "The printed schedule is on the notice board.",
          "hypothesis": "El horario impreso está en el tablón de anuncios."}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_rows(path):
    return [json.loads(s) for s in Path(path).read_text(encoding="utf-8").splitlines() if s.strip()]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def schedule(primary, disputed, regression, seed):
    plan = [("warmup", 0, arm, WARMUP) for arm in ("baseline", "candidate")]
    for repeat in (1, 2):
        items = list(enumerate(primary))
        random.Random(seed + repeat).shuffle(items)
        for i, e in items:
            arms = ("baseline", "candidate") if (i + repeat) % 2 else ("candidate", "baseline")
            plan.extend(("holdout", repeat, arm, e) for arm in arms)
    for i, e in enumerate(disputed):
        arms = ("baseline", "candidate") if i % 2 else ("candidate", "baseline")
        plan.extend(("disputed", 1, arm, e) for arm in arms)
    plan.extend(("development", 1, "candidate", e) for e in regression)
    return plan


def key(item):
    phase, repeat, arm, e = item
    return f"{phase}:{repeat}:{arm}:{e['id']}"


def load_study(path):
    spec = read_json(path)
    if spec["repeats"] != 2:
        raise ValueError("this counterbalanced protocol requires exactly two passes")
    profiles = {}
    for arm, config in spec["arms"].items():
        profile = read_json(ROOT / config["profile"])
        for field in ("base_url", "model"):
            profile[field] = os.environ.get(profile.get(field + "_env", ""), profile.get(field))
            if not profile[field]:
                raise ValueError("explicit endpoint/model required")
        profiles[arm] = profile
    if set(profiles) != {"baseline", "candidate"} or profiles["baseline"] != profiles["candidate"]:
        raise ValueError("this ablation changes only guideline text; resolved profiles must be identical")
    primary, disputed, regression = [read_rows(ROOT / spec[name]) for name in ("holdout", "disputed", "regression")]
    prior = [row for file in spec["prior_datasets"] for row in read_rows(ROOT / file)]
    validate_sets(primary, disputed, regression, prior)
    return spec, profiles, primary, disputed, regression


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", default="evals/studies/severity-v3.json")
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    study_path = Path(args.study).resolve()
    spec, profiles, primary, disputed, regression = load_study(study_path)
    plan = schedule(primary, disputed, regression, spec["order_seed"])
    files = set(SOURCE_FILES + spec["prior_datasets"] + [study_path.relative_to(ROOT).as_posix()])
    files.update(spec[name] for name in ("holdout", "disputed", "regression"))
    files.update(config[name] for config in spec["arms"].values() for name in ("profile", "guideline"))
    prior_hashes = {}
    for directory in spec["prior_runs"]:
        artifact_file = ROOT / directory / "artifact-sha256.json"
        prior_hashes.update(read_json(artifact_file))
        prior_hashes[artifact_file.relative_to(ROOT).as_posix()] = digest(artifact_file)
    for path, expected in prior_hashes.items():
        if digest(ROOT / path) != expected:
            raise ValueError("prior evidence changed: " + path)
    identity = {"spec": spec, "profiles": profiles, "files": {f: digest(ROOT / f) for f in sorted(files)},
                "provenance": read_json(args.metadata), "prior_artifact_hashes": prior_hashes,
                "order": [key(item) for item in plan]}
    out = Path(args.out).resolve()
    if args.resume:
        manifest = read_json(out / "manifest.json")
        if manifest["identity"] != identity:
            parser.error("resume identity changed; use a new run")
    else:
        out.mkdir(parents=True, exist_ok=False)
        manifest = {"identity": identity, "status": "running", "started_at_utc": datetime.now(timezone.utc).isoformat(),
                    "python": platform.python_version(), "platform": platform.platform(), "automatic_retries": 0,
                    "host_load": "Interactive desktop; background host load, power and thermals are not isolated."}
        for relative in files:
            target = out / "source-snapshot" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        write_json(out / "manifest.json", manifest)
    raw = out / "attempts.jsonl"
    records = read_rows(raw) if raw.exists() else []
    if [r["key"] for r in records] != identity["order"][:len(records)]:
        parser.error("saved attempts must be a unique prefix of the frozen order")
    lock = out / ".writer-lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        parser.error("run already has a writer lock; verify no process owns it before recovering")
    os.close(fd)
    try:
        traces, judges = [], {}
        for arm, profile in profiles.items():
            request = profile["request"]
            judges[arm] = OpenAIJudge(profile["base_url"], model=profile["model"],
                api_key=os.environ.get("ANOTA_JUDGE_API_KEY", "EMPTY"),
                guideline_text=(out / "source-snapshot" / spec["arms"][arm]["guideline"]).read_text(encoding="utf-8"),
                timeout=request["timeout_seconds"], max_tokens=request["max_tokens"],
                structured_output=request["structured_output"], prompt_suffix=request["prompt_suffix"], observer=traces.append)
        failures = 0
        with raw.open("a", encoding="utf-8") as handle:
            for item in plan[len(records):]:
                phase, repeat, arm, example = item
                traces.clear()
                verdict, error = None, None
                try:
                    verdict = judges[arm].evaluate(example, [])
                except JudgeUnavailable as exc:
                    error = str(exc)
                record = {"key": key(item), "phase": phase, "repeat": repeat, "arm": arm,
                          "task_id": example["id"], "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                          "verdict": verdict, "error": error, "trace": traces[-1]}
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                records.append(record)
                write_json(out / "summary.json", severity_summary(records, primary, disputed, regression, spec["gates"]))
                print(f"{len(records)}/{len(plan)} {key(item)} {'valid' if verdict else 'FAILED'} {traces[-1]['elapsed_seconds']:.2f}s", flush=True)
                failures = failures + 1 if error else 0
                if failures >= 3:
                    manifest["status"] = "stopped_after_three_failures"
                    write_json(out / "manifest.json", manifest)
                    raise SystemExit("Stopped; failures preserved")
        manifest.update(status="complete", finished_at_utc=datetime.now(timezone.utc).isoformat())
        write_json(out / "manifest.json", manifest)
    finally:
        lock.unlink()


if __name__ == "__main__":
    main()
