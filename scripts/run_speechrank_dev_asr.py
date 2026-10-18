"""Durable dev-only confidence capture; never reads references or runs test ASR."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from speechprep.asr import ModelSpec
from speechprep.manifest import atomic_jsonl, file_sha256, read_manifest
from speechprep.pipeline import default_config, run_pipeline
from speechprep.state import exclusive_run

REVISION = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"


def utc():
    return datetime.now(UTC).isoformat()


def save(path, value):
    atomic_jsonl(path, [value])


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_dev_rows(rows):
    if not rows or len({r["item_id"] for r in rows}) != len(rows):
        raise ValueError("empty or duplicate development rows")
    for row in rows:
        if (row.get("split") not in {"dev-clean", "dev-other"}
                or row.get("language") != "en"
                or not row["item_id"].startswith("librispeech:" + row["split"] + ":")
                or any(k in row for k in ("text", "reference_text", "asr_text", "errors"))):
            raise ValueError("only label-free official development manifests are allowed")


def confidence_config():
    config = default_config(cpu_threads=4)
    config["adapter_version"] = "speechrank-faster-whisper-confidence-v2"
    return config


def worker(manifest, folder):
    from speechrank.confidence_asr import ConfidenceAdapter

    validate_dev_rows(read_manifest(manifest))
    summary = run_pipeline(manifest, folder / "asr.jsonl", folder / "asr-state.db",
                           ModelSpec("base", REVISION, "Systran/faster-whisper-base"),
                           confidence_config(), adapter_factory=ConfidenceAdapter)
    save(folder / "summary.json", summary)
    print(json.dumps(summary), flush=True)
    return 0 if summary["complete"] else 2


def progress(jobs):
    report = []
    for job in jobs:
        counts = {}
        state = job["folder"] / "asr-state.db"
        if state.is_file():
            try:
                with sqlite3.connect(f"{state.as_uri()}?mode=ro", uri=True, timeout=1) as db:
                    counts = dict(db.execute("SELECT status,COUNT(*) FROM items GROUP BY status"))
            except sqlite3.Error:
                pass
        report.append({"worker": job["index"], "counts": counts,
                       "returncode": job["process"].poll()})
    return report


def run(manifest, folder):
    from huggingface_hub import snapshot_download

    folder.mkdir(parents=True, exist_ok=True)
    with exclusive_run(folder / "parent-run"):
        rows = read_manifest(manifest)
        validate_dev_rows(rows)
        identity = {"manifest_sha256": file_sha256(manifest), "config": confidence_config(),
                    "model_revision": REVISION, "expected_items": len(rows)}
        state_path = folder / "run.json"
        old = read_json(state_path) if state_path.exists() else None
        if old and old["identity"] != identity:
            raise ValueError("existing dev run has different inputs/configuration")
        sources = [*sorted((ROOT / "speechprep").rglob("*.py")),
                   ROOT / "speechrank/__init__.py", ROOT / "speechrank/confidence_asr.py",
                   Path(__file__).resolve()]
        hashes = {p.relative_to(ROOT).as_posix(): file_sha256(p) for p in sources}
        if old and old["source_hashes"] != hashes:
            raise ValueError("dev inference source changed on resume")
        for source in sources:
            target = folder / "source-snapshot" / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and file_sha256(target) != file_sha256(source):
                raise ValueError("existing source snapshot differs")
            if not target.exists():
                shutil.copy2(source, target)
        snapshot = Path(snapshot_download("Systran/faster-whisper-base", revision=REVISION,
                                         local_files_only=True,
                                         allow_patterns=["config.json", "model.bin", "tokenizer.json",
                                                         "vocabulary.*", "preprocessor_config.json"]))
        model_hashes = {p.name: file_sha256(p) for p in sorted(snapshot.iterdir()) if p.is_file()}
        if old and old["model_file_hashes"] != model_hashes:
            raise ValueError("local ASR model changed on resume")
        for row in rows:
            if file_sha256(row["audio_path"]) != row["audio_sha256"]:
                raise ValueError("development audio changed")
        if old and old["status"] == "completed":
            if file_sha256(folder / "asr.jsonl") != old["results_sha256"]:
                raise ValueError("completed output changed")
            return old
        record = {"status": "running", "started_at_utc": utc(), "identity": identity,
                  "manifest": str(manifest), "source_hashes": hashes, "model_file_hashes": model_hashes,
                  "model_snapshot": str(snapshot), "workers": 3, "python": sys.version,
                  "test_asr_calls": 0, "reference_labels_opened": False,
                  "prior_attempts": [*old.get("prior_attempts", []),
                                     {k: v for k, v in old.items() if k != "prior_attempts"}] if old else []}
        save(state_path, record)
        jobs = []
        started = time.perf_counter()
        try:
            for index in range(3):
                child = folder / f"worker-{index}"
                child.mkdir(exist_ok=True)
                selected = rows[index::3]
                shard = child / "manifest.jsonl"
                atomic_jsonl(shard, selected)
                command = [sys.executable, "-u", str(Path(__file__).resolve()), "worker",
                           "--manifest", str(shard), "--output-dir", str(child)]
                with (child / "stdout.log").open("a", encoding="utf-8") as out, \
                        (child / "stderr.log").open("a", encoding="utf-8") as err:
                    process = subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=err,
                        env={**os.environ, "HF_HUB_OFFLINE": "1", "OMP_NUM_THREADS": "4"},
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                jobs.append({"index": index, "folder": child, "process": process,
                             "ids": {r["item_id"] for r in selected}})
            next_log = 0
            while any(j["process"].poll() is None for j in jobs):
                if time.perf_counter() >= next_log:
                    save(folder / "progress.json", {"at_utc": utc(), "workers": progress(jobs)})
                    print(json.dumps(read_json(folder / "progress.json")), flush=True)
                    next_log = time.perf_counter() + 30
                time.sleep(1)
            combined, summaries = {}, []
            source_by_id = {row["item_id"]: row for row in rows}
            for job in jobs:
                if job["process"].returncode != 0:
                    raise RuntimeError(f"dev worker {job['index']} failed; inspect stderr.log")
                summary = read_json(job["folder"] / "summary.json")
                result_rows = [json.loads(x) for x in (job["folder"] / "asr.jsonl").read_text(
                    encoding="utf-8").splitlines() if x.strip()]
                if (not summary["complete"] or summary["total"] != len(job["ids"])
                        or len(result_rows) != len(job["ids"])
                        or {r["item_id"] for r in result_rows} != job["ids"]):
                    raise ValueError("dev worker output incomplete or mismatched")
                for row in result_rows:
                    if row["item_id"] in combined or row["run_id"] != summary["run_id"]:
                        raise ValueError("duplicate or stale dev row")
                    if row["config"] != identity["config"] or row["model"]["revision"] != REVISION:
                        raise ValueError("dev row model/config mismatch")
                    source = source_by_id[row["item_id"]]
                    expected_model = {"name": "base", "revision": REVISION,
                                      "repo_id": "Systran/faster-whisper-base",
                                      "device": "cpu", "compute_type": "int8"}
                    if (row["model"] != expected_model or row["audio_sha256"] != source["audio_sha256"]
                            or row["language"] != source["language"]
                            or row["status"] not in {"succeeded", "failed"}
                            or (row["status"] == "succeeded" and not isinstance(row.get("asr_text"), str))):
                        raise ValueError("dev row identity or terminal result mismatch")
                    combined[row["item_id"]] = row
                summaries.append(summary)
            if set(combined) != {r["item_id"] for r in rows}:
                raise ValueError("dev merged IDs mismatch")
            if file_sha256(manifest) != identity["manifest_sha256"]:
                raise ValueError("dev source manifest changed")
            if any(file_sha256(ROOT / p) != h for p, h in hashes.items()):
                raise ValueError("dev inference source changed")
            if any(file_sha256(snapshot / p) != h for p, h in model_hashes.items()):
                raise ValueError("dev ASR model changed")
            if any(file_sha256(r["audio_path"]) != r["audio_sha256"] for r in rows):
                raise ValueError("development audio changed during inference")
            atomic_jsonl(folder / "asr.jsonl", [combined[r["item_id"]] for r in rows])
            record.update(status="completed", completed_at_utc=utc(),
                          elapsed_seconds=time.perf_counter() - started,
                          results_sha256=file_sha256(folder / "asr.jsonl"), summaries=summaries,
                          new_dev_asr_calls=sum(s["new_inference"] for s in summaries))
            save(state_path, record)
            print(json.dumps({"status": "completed", "items": len(rows),
                              "new_dev_asr_calls": record["new_dev_asr_calls"], "test_asr_calls": 0}), flush=True)
            return record
        except BaseException as exc:
            record.update(status="failed", stopped_at_utc=utc(), error=f"{type(exc).__name__}: {exc}")
            save(state_path, record)
            raise
        finally:
            for job in jobs:
                if job["process"].poll() is None:
                    job["process"].terminate()
            for job in jobs:
                job["process"].wait(timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "worker"])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest, folder = args.manifest.resolve(), args.output_dir.resolve()
    if args.command == "worker":
        return worker(manifest, folder)
    run(manifest, folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
