"""QC import/policy/exposure tests use a frozen synthetic report, no ML runtime.

The report fixture was generated from a 1-second all-zero WAV. It tests the
storage and presentation contract, not VAD accuracy or human review quality.
"""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import export, speech
from app.main import create_app
from speechqc.schema import transcript_digest
from tests.test_speech_backend import manifest


def seal(report):
    body = {key: value for key, value in report.items() if key != "analysis_id"}
    report["analysis_id"] = hashlib.sha256(json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    return report


def qc_manifest(tmp_path):
    path, row = manifest(tmp_path, reference="SECRET_REFERENCE",
                         metadata={"answer": "SECRET_REFERENCE"})
    report = json.loads((Path(__file__).parent / "fixtures" / "audio-qc-silence-v1.json").read_text())
    report["audio_sha256"] = row["audio_sha256"]
    report["transcript_sha256"] = transcript_digest(row)
    row["audio_analysis"] = seal(report)
    write_row(path, row)
    return path, row


def write_row(path, row):
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")


def prepared(tmp_path, hints):
    app = create_app(db_path=str(tmp_path / "qc.db"), export_dir=str(tmp_path / "exports"))
    path, row = qc_manifest(tmp_path)
    result = speech.import_speech_manifest(app.state.db, path, "qc", show_suggestions=hints)
    assert result["n"] == 1, result
    client = TestClient(app)
    task = client.post("/api/claim", json={"annotator": "qc-test"}).json()["task"]
    return app, client, task, path, row


def submission(task, **changes):
    report = task["speech"].get("audio_analysis")
    body = {
        "annotator": "qc-test", "assignment_id": task["assignment_id"],
        "corrected_transcript": "", "elapsed_ms": 1200, "active_ms": 700,
        "review_context": {"analysis_id": report["analysis_id"] if report else None,
                           "hints_exposed": bool(report), "played_issue_ids": []},
    }
    body.update(changes)
    return body


@pytest.mark.parametrize("hints", [False, True])
def test_qc_claim_undo_policy_and_deterministic_export(tmp_path, hints):
    app, client, task, path, row = prepared(tmp_path, hints)
    view = task["speech"]
    assert view["waveform"]["peaks"]
    assert ("audio_analysis" in view) == hints
    assert "SECRET_REFERENCE" not in json.dumps(task)
    assert "audio_path" not in json.dumps(task)
    assert "detectors" not in json.dumps(task)
    if not hints:
        assert all(key not in view for key in ("issues", "vad", "metrics", "coverage", "warnings"))
    body = submission(task)
    if hints:
        issue_id = view["audio_analysis"]["issues"][0]["id"]
        body["review_context"]["played_issue_ids"] = [issue_id]
    assert client.post("/api/speech/submit", json=body).status_code == 200
    first, second = export.build_snapshot(app.state.db), export.build_snapshot(app.state.db)
    assert first == second
    item = first["items"][0]
    assert item["task"]["provenance"]["audio_analysis"] == row["audio_analysis"]
    context = item["annotations"][0]["review_context"]
    assert context["hints_exposed"] == hints
    assert context["telemetry_status"] == "reported"
    assert context["played_issue_ids"] == body["review_context"]["played_issue_ids"]
    undone = client.post("/api/undo", json={"annotator": "qc-test"}).json()["task"]
    assert ("audio_analysis" in undone["speech"]) == hints
    assert client.post("/api/speech/submit", json=submission(undone)).status_code == 200
    assert len(export.build_snapshot(app.state.db)["items"][0]["annotations"]) == 2
    assert speech.import_speech_manifest(app.state.db, path, "qc")["skipped"] == 1


def test_no_optional_audio_libraries_loaded_by_web_process():
    command = (
        "import sys; from app.main import create_app; create_app(); "
        "assert not {'numpy','soundfile','faster_whisper','onnxruntime','torch'} & set(sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", command], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_hint_policy_cannot_silently_change_existing_review_batch(tmp_path):
    app, _client, _task, path, _row = prepared(tmp_path, False)
    with pytest.raises(ValueError, match="hint policy differs"):
        speech.import_speech_manifest(app.state.db, path, "qc", show_suggestions=True)
    assert not app.state.db.one("SELECT show_suggestions FROM batches")["show_suggestions"]


@pytest.mark.parametrize("change", ["audio", "transcript", "duration", "tamper", "unknown"])
def test_reject_mismatched_or_poisoned_analysis_without_new_task(tmp_path, change):
    app, _client, _task, path, row = prepared(tmp_path, True)
    broken = copy.deepcopy(row)
    if change == "audio":
        broken["audio_analysis"]["audio_sha256"] = "0" * 64
        seal(broken["audio_analysis"])
    elif change == "transcript":
        broken["asr_text"] = "changed hypothesis"
    elif change == "duration":
        broken["audio_duration_s"] = 2
    elif change == "tamper":
        broken["audio_analysis"]["waveform"]["peaks"][0][0] = -0.5
    else:
        broken["audio_analysis"]["reference"] = "SECRET_REFERENCE"
        seal(broken["audio_analysis"])
    write_row(path, broken)
    result = speech.import_speech_manifest(app.state.db, path, "qc")
    assert result["invalid"] == 1 and result["n"] == 0, result
    assert app.state.db.one("SELECT COUNT(*) n FROM tasks")["n"] == 1


@pytest.mark.parametrize("hints", [False, True])
def test_reject_forged_exposure_or_playback_ids(tmp_path, hints):
    app, client, task, _path, _row = prepared(tmp_path, hints)
    body = submission(task)
    body["review_context"]["played_issue_ids"] = ["not-a-visible-issue"]
    assert client.post("/api/speech/submit", json=body).status_code == 409
    body = submission(task)
    body["review_context"]["hints_exposed"] = not hints
    assert client.post("/api/speech/submit", json=body).status_code == 409
    body = submission(task)
    body["review_context"]["analysis_id"] = "0" * 64
    assert client.post("/api/speech/submit", json=body).status_code == 409
    assert app.state.db.one("SELECT COUNT(*) n FROM speech_annotations")["n"] == 0


def test_legacy_client_exposure_is_unknown_instead_of_false(tmp_path):
    app, client, task, _path, _row = prepared(tmp_path, True)
    body = submission(task)
    body.pop("review_context")
    assert client.post("/api/speech/submit", json=body).status_code == 200
    context = export.build_snapshot(app.state.db)["items"][0]["annotations"][0]["review_context"]
    assert context["hints_available"] is True
    assert context["hints_exposed"] is None
    assert context["telemetry_status"] == "not_reported"


def test_analysis_failure_is_preserved_without_leaking_offline_diagnostics(tmp_path):
    app = create_app()
    failure = {"type": "ValueError", "message": "PRIVATE_LOCAL_PATH: resource bound exceeded"}
    path, _row = manifest(tmp_path, audio_analysis_error=failure)
    assert speech.import_speech_manifest(app.state.db, path, "qc-failure", show_suggestions=True)["n"] == 1
    client = TestClient(app)
    task = client.post("/api/claim", json={"annotator": "qc-test"}).json()["task"]
    assert "PRIVATE_LOCAL_PATH" not in json.dumps(task)
    assert "audio_analysis" not in task["speech"]
    item = export.build_snapshot(app.state.db)["items"][0]
    assert item["task"]["provenance"]["audio_analysis_error"] == failure
