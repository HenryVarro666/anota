"""Exercise a CPU release using generated fixtures, without tests or evals.

Run from the checkout root: python scripts/verify_release_image.py
Run the exact image: docker run --rm -i --entrypoint python anota - \
    --require-cpu-only < scripts/verify_release_image.py

The synthetic QC report below tests the import/presentation contract; it is not
an acoustic analysis or a claim about detector quality. No model is downloaded.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import wave
from pathlib import Path

# stdin execution has no source location; the image WORKDIR is the app root.
sys.path.insert(0, str(Path.cwd()))

OPTIONAL_LIBRARIES = {"numpy", "soundfile", "faster_whisper", "onnxruntime",
                      "torch", "joblib", "sklearn", "threadpoolctl"}


def fixture(root: Path) -> Path:
    from speechqc.schema import (
        ANALYZER_VERSION,
        DEFAULT_CONFIG,
        digest_json,
        report_digest,
        transcript_digest,
    )

    audio = root / "synthetic.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 16000)
    row = {
        "schema_version": "speech-v1", "item_id": "synthetic-release-001",
        "language": "en", "audio_path": audio.name,
        "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "corpus": "synthetic-release-check", "corpus_revision": "v1", "split": "test",
        "source_id": "001", "reference_id": "PRIVATE_REFERENCE_ID", "license": "CC0-1.0",
        "reference": "PRIVATE_REFERENCE_TEXT", "metadata": {"answer": "PRIVATE_REFERENCE_TEXT"},
        "status": "succeeded", "asr_text": "synthetic transcript",
        "segments": [{"start": 0.0, "end": 1.0, "text": "synthetic transcript",
                      "avg_logprob": -0.5}],
        "words": [{"start": 0.0, "end": 1.0, "w": "synthetic", "probability": 0.8}],
        "audio_duration_s": 1.0, "sample_rate": 16000, "channels": 1,
        "model": {"name": "synthetic", "revision": "v1", "device": "cpu", "compute_type": "int8"},
        "config": {}, "cache_key": "synthetic-cache", "run_id": "synthetic-release-check",
        "processing_ms": 1, "cache_hit": False, "error": None,
    }
    issue = {"code": "low_level", "start": 0.0, "end": 1.0, "priority": "low",
             "message": "Synthetic fixture: check recording level.",
             "evidence": {"rms_dbfs": None, "threshold_dbfs": -35.0}}
    issue["id"] = digest_json(issue)
    report = {
        "schema_version": "speechqc-v1", "analyzer_version": ANALYZER_VERSION,
        "audio_sha256": row["audio_sha256"], "transcript_sha256": transcript_digest(row),
        "duration_s": 1.0, "sample_rate": 16000, "channels": 1,
        "config": dict(DEFAULT_CONFIG),
        "detectors": [{"name": "speechqc-dsp", "version": "synthetic", "model_sha256": None}],
        "status": "partial", "waveform": {"duration_s": 1.0, "channels": 1, "peaks": [[0.0, 0.0]]},
        "metrics": {"peak_dbfs": None, "rms_dbfs": None, "near_full_scale_ratio": 0.0,
                    "low_energy_ratio": 1.0, "dc_offset": 0.0},
        "vad": {"status": "unavailable", "intervals": [], "error": "Synthetic fixture; no VAD run."},
        "coverage": {"status": "unavailable", "basis": "segments", "speech_s": None,
                     "covered_s": None, "uncovered_s": None, "uncovered_ratio": None,
                     "text_outside_s": None},
        "issues": [issue], "warnings": ["Synthetic release test; not detector evidence."],
    }
    report["analysis_id"] = report_digest(report)
    row["audio_analysis"] = report
    manifest = root / "manifest.jsonl"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return manifest


def checked(response):
    assert response.status_code == 200, (response.status_code, response.text)
    return response.json()


def check_review(root: Path, hints: bool, fallback_reason: str):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.speech import import_speech_manifest
    from speechprep.manifest import read_manifest

    root.mkdir()
    manifest = fixture(root)
    assert read_manifest(manifest)[0]["item_id"] == "synthetic-release-001"
    app = create_app(db_path=str(root / "release.db"), export_dir=str(root / "exports"))
    try:
        imported = import_speech_manifest(app.state.db, manifest, "release", show_suggestions=hints)
        assert imported["n"] == 1 and imported["invalid"] == 0, imported
        bid = imported["batch_id"]
        with TestClient(app) as client:
            assert checked(client.get("/api/health"))["judge"]["mode"] == "mock"
            assert client.get("/").status_code == 200
            queue = checked(client.get("/api/speech/review/queue", params={
                "annotator": "release-check", "batch_id": bid, "sort": "ml"}))
            assert queue["total"] == 1
            if hints:
                ranking = queue["items"][0]["ranking"]
                assert ranking["method"] == "qc" and ranking["reason"] == fallback_reason, ranking
                assert ranking["density"] is None and ranking["qc_score"] > 0
            else:
                assert "ranking" not in queue["items"][0] and queue["counts"]["blind"] == 1
            task = checked(client.post("/api/claim", json={
                "annotator": "release-check", "batch_id": bid}))["task"]
            assert task["task_id"] == queue["items"][0]["task_id"]
            assert ("audio_analysis" in task["speech"]) == hints
            assert task["speech"]["waveform"]["peaks"]
            for value in (queue, task):
                encoded = json.dumps(value)
                assert not any(secret in encoded for secret in (
                    "PRIVATE_REFERENCE", "reference_id", "audio_path", "avg_logprob", "probability"))
            if not hints:
                assert not {"issues", "metrics", "vad", "coverage", "warnings"} & task["speech"].keys()
            audio = client.get(task["speech"]["audio_url"])
            assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
            partial = client.get(task["speech"]["audio_url"], headers={"Range": "bytes=0-43"})
            assert partial.status_code == 206 and len(partial.content) == 44

            def submit(current, text):
                report = current["speech"].get("audio_analysis")
                return checked(client.post("/api/speech/submit", json={
                    "annotator": "release-check", "assignment_id": current["assignment_id"],
                    "corrected_transcript": text, "elapsed_ms": 100, "active_ms": 80,
                    "review_context": {"analysis_id": report["analysis_id"] if report else None,
                                       "hints_exposed": hints, "played_issue_ids": []}}))

            submit(task, "synthetic correction one")
            reopened = checked(client.post("/api/undo", json={"annotator": "release-check"}))["task"]
            assert reopened["speech"]["latest_annotation"]["corrected_transcript"] == "synthetic correction one"
            assert ("audio_analysis" in reopened["speech"]) == hints
            submit(reopened, "synthetic correction two")
            first = checked(client.post("/api/export", json={"batch_id": bid}))
            second = checked(client.post("/api/export", json={"batch_id": bid}))
            assert first["sha256"] == second["sha256"]
            snapshot = checked(client.get(first["download_url"]))
            item = snapshot["items"][0]
            assert len(item["annotations"]) == 2
            assert item["final_label"]["corrected_transcript"] == "synthetic correction two"
            assert item["task"]["provenance"]["audio_analysis"]["analysis_id"]
    finally:
        app.state.db.conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-cpu-only", action="store_true",
                        help="Also require that ASR/ML optional packages are not installed.")
    parser.add_argument("--ranking-bundle", type=Path,
                        help="Read-only reviewed bundle; with --require-cpu-only, verify dependency fallback.")
    args = parser.parse_args()
    if args.ranking_bundle:
        if not args.require_cpu_only:
            parser.error("--ranking-bundle requires --require-cpu-only")
        assert args.ranking_bundle.is_dir(), "Reviewed model bundle directory does not exist"
    if args.require_cpu_only:
        installed = sorted(name for name in OPTIONAL_LIBRARIES if importlib.util.find_spec(name))
        assert not installed, f"Unexpected optional packages in CPU image: {installed}"
        from speechrank.runtime import RuntimeUnavailable, _dependencies
        try:
            _dependencies()
        except RuntimeUnavailable as exc:
            assert str(exc) == "optional_dependencies_unavailable"
        else:
            raise AssertionError("CPU image unexpectedly loaded optional ranking dependencies")
    os.environ["ANOTA_JUDGE"] = "mock"
    with tempfile.TemporaryDirectory(prefix="anota-release-") as directory:
        root = Path(directory)
        os.environ["ANOTA_SPEECHRANK_BUNDLE"] = str(args.ranking_bundle or root / "absent-optional-bundle")
        reason = "optional_dependencies_unavailable" if args.ranking_bundle else "bundle_missing"
        check_review(root / "hinted", hints=True, fallback_reason=reason)
        check_review(root / "blind", hints=False, fallback_reason=reason)
    assert not OPTIONAL_LIBRARIES & sys.modules.keys(), "Web review eagerly imported optional libraries"
    assert not any(name == "scripts" or name.startswith("scripts.") for name in sys.modules)
    print(f"Release check passed: queue, QC fallback ({reason}), blind policy, audio, submit, undo and export.")


if __name__ == "__main__":
    main()
