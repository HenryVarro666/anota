"""Exercise real ASR recovery/cache and the HTTP lifecycle on the 20-clip smoke set.

Corrections in the isolated verification database are AUTOMATED TEST EDITS.
They are never human labels and are never imported into the listening database.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODEL_REVISION = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_cli(command, evidence, name):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=600, check=False,
                            env={**os.environ, "PYTHONUTF8": "1", "HF_HUB_DISABLE_PROGRESS_BARS": "1"})
    (evidence / f"{name}.stdout.log").write_bytes(result.stdout)
    (evidence / f"{name}.stderr.log").write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f"{name} failed: {result.stderr[-2000:]!r} {result.stdout[-2000:]!r}")
    summary = json.loads(result.stdout.decode("utf-8").strip().splitlines()[-1])
    write_json(evidence / f"{name}.json", summary)
    print(json.dumps({"phase": name, **summary}), flush=True)
    return summary


def hard_interrupt(command, state, evidence):
    with (evidence / "interrupted.stdout.log").open("w", encoding="utf-8") as stdout, \
         (evidence / "interrupted.stderr.log").open("w", encoding="utf-8") as stderr:
        worker = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                                  env={**os.environ, "PYTHONUTF8": "1", "HF_HUB_DISABLE_PROGRESS_BARS": "1"})
        running = []
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if worker.poll() is not None:
                    raise RuntimeError("ASR finished before an in-flight item could be interrupted")
                try:
                    with sqlite3.connect(state, timeout=0.5) as db:
                        running = db.execute("SELECT item_id,cache_key FROM items WHERE status='running'").fetchall()
                except sqlite3.OperationalError:
                    running = []
                if running:
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("ASR never persisted a running item")
        finally:
            if worker.poll() is None:
                if os.name == "nt":
                    # venv Python may have a launcher child: terminate only this
                    # owned worker's process tree so the OS releases its lock.
                    subprocess.run(["taskkill", "/PID", str(worker.pid), "/T", "/F"],
                                   capture_output=True, check=True)
                else:
                    worker.kill()
                worker.wait(timeout=15)
        assert running and worker.returncode != 0
        result = {"pid": worker.pid, "returncode": worker.returncode,
                  "persisted_running_items": [row[0] for row in running], "hard_kill": True}
        write_json(evidence / "hard-interruption.json", result)
        return result


def verify_http(results, evidence):
    os.environ["ANOTA_JUDGE"] = "mock"  # No LLM is used in this workflow.
    from fastapi.testclient import TestClient

    from app.export import canonical_json
    from app.main import create_app
    from app.speech import import_speech_manifest

    db_path = evidence / "workflow-test.db"
    app = create_app(db_path=str(db_path), export_dir=str(evidence / "exports"))
    imported = import_speech_manifest(app.state.db, results, "real-audio-workflow-test")
    assert imported["n"] == 20 and not imported["failed"] and not imported["invalid"], imported
    repeated = import_speech_manifest(app.state.db, results, "real-audio-workflow-test")
    assert repeated["n"] == 0 and repeated["skipped"] == 20
    client = TestClient(app)
    original_rows = {r["item_id"]: r for r in map(json.loads, results.read_text(encoding="utf-8").splitlines())}
    count = 0
    audio_bytes = 0
    while True:
        claimed = client.post("/api/claim", json={"annotator": "automated-workflow-test"}).json()
        task = claimed["task"]
        if task is None:
            assert claimed["progress"] == {"done": 20, "total": 20}
            break
        assert "reference" not in task and "metadata" not in task and "suggestions" not in task
        assert "audio_path" not in json.dumps(task)
        raw = task["speech"]["asr_text"]
        response = client.get(task["speech"]["audio_url"])
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == task["speech"]["audio_id"].removeprefix("audio-")
        audio_bytes += len(response.content)
        payload = {"annotator": "automated-workflow-test", "assignment_id": task["assignment_id"],
                   "corrected_transcript": raw + "\n[automated integration edit]",
                   "note": "AUTOMATED TEST EDIT; no human listened or approved this transcript.",
                   "elapsed_ms": 0, "active_ms": 0}
        assert client.post("/api/speech/submit", json=payload).status_code == 200
        if count == 0:
            undo = client.post("/api/undo", json={"annotator": payload["annotator"]}).json()["task"]
            assert undo["speech"]["asr_text"] == raw
            assert undo["speech"]["latest_annotation"]["corrected_transcript"] == payload["corrected_transcript"]
            payload["corrected_transcript"] += " [resubmitted]"
            assert client.post("/api/speech/submit", json=payload).status_code == 200
        count += 1
    exports = [client.post("/api/export", json={"batch_id": imported["batch_id"]}).json() for _ in range(2)]
    assert exports[0]["sha256"] == exports[1]["sha256"]
    snap = client.get(exports[0]["download_url"]).json()
    content = {key: value for key, value in snap.items() if key not in ("version", "sha256")}
    assert hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest() == snap["sha256"]
    for item in snap["items"]:
        original = original_rows[item["task"]["provenance"]["item_id"]]
        assert item["task"]["asr_text"] == original["asr_text"]
        assert item["task"]["provenance"]["audio_sha256"] == original["audio_sha256"]
        assert item["final_label"]["note"].startswith("AUTOMATED TEST EDIT")
    assert len(snap["items"]) == 20
    annotations = app.state.db.one("SELECT COUNT(*) n FROM speech_annotations")["n"]
    assert annotations == 21
    app.state.db.conn.close()
    restored = create_app(db_path=str(db_path))
    assert TestClient(restored).post("/api/claim", json={"annotator": "automated-workflow-test"}).json()["task"] is None
    restored.state.db.conn.close()
    return {"import": imported, "duplicate_import": repeated, "audio_downloads_verified": count,
            "audio_bytes": audio_bytes, "automated_annotations": annotations,
            "human_annotations": 0, "undo_resubmit": True, "database_restart": True,
            "raw_asr_preserved": True, "reference_isolated": True,
            "stable_export_sha256": exports[0]["sha256"], "exports": exports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="runtime/speech-smoke")
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    evidence = directory / "verification" / uuid.uuid4().hex[:12]
    evidence.mkdir(parents=True)
    # Use a fresh state when re-verifying an already complete data set, so the
    # interruption really exercises missing inference instead of a warm replay.
    state = directory / "asr-state.db"
    if state.exists():
        with sqlite3.connect(state) as db:
            cached = db.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        if cached >= 20:
            state = evidence / "asr-state.db"
    results = directory / "asr.jsonl"
    command = [sys.executable, "-m", "speechprep", "transcribe", "--manifest", str(directory / "manifest.jsonl"),
               "--output", str(results), "--state", str(state), "--model", "base",
               "--revision", MODEL_REVISION, "--device", "cpu", "--compute-type", "int8"]
    initial = run_cli(command + ["--limit", "2"], evidence, "partial")
    assert 0 < initial["succeeded"] < initial["total"] and initial["failed"] == 0, initial
    interrupted = hard_interrupt(command, state, evidence)
    resumed = run_cli(command, evidence, "resumed")
    assert resumed["recovered"] >= 1 and resumed["succeeded"] == 20 and resumed["failed"] == 0, resumed
    warm = run_cli(command, evidence, "warm-cache")
    assert warm["cache_hits"] == 20 and warm["new_inference"] == 0 and warm["failed"] == 0, warm
    http = verify_http(results, evidence)
    report = {"real_asr": True, "model_revision": MODEL_REVISION, "partial": initial,
              "hard_interruption": interrupted, "resumed": resumed, "warm_cache": warm,
              "http_workflow": http, "evidence_directory": str(evidence)}
    write_json(evidence / "workflow-verification.json", report)
    write_json(directory / "workflow-verification.json", report)
    print(json.dumps({"verified": True, "report": str(directory / "workflow-verification.json")}), flush=True)


if __name__ == "__main__":
    main()
