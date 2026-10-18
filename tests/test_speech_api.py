"""HTTP listening workflow, using generated WAV solely as a test fixture."""
import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.speech import import_speech_manifest
from tests.test_speech_backend import manifest


def setup(tmp_path):
    app = create_app(db_path=str(tmp_path / "speech.db"), export_dir=str(tmp_path / "exports"))
    path, row = manifest(tmp_path, reference="SECRET_REFERENCE",
                         metadata={"reference": "SECRET_REFERENCE"})
    imported = import_speech_manifest(app.state.db, path, "speech-api")
    return app, TestClient(app), imported, row


def claim(client, annotator="api-test"):
    response = client.post("/api/claim", json={"annotator": annotator})
    assert response.status_code == 200
    return response.json()["task"]


def submit(client, task, transcript="你好。", **changes):
    return client.post("/api/speech/submit", json={
        "annotator": "api-test", "assignment_id": task["assignment_id"],
        "corrected_transcript": transcript, "elapsed_ms": 1000, "active_ms": 500,
        "note": "automated integration test, not human review", **changes})


def test_audio_claim_edit_undo_export_and_restart(tmp_path):
    app, client, imported, row = setup(tmp_path)
    task = claim(client)
    assert task["task_type"] == "speech"
    assert "SECRET_REFERENCE" not in json.dumps(task)
    assert "suggestions" not in task and "reference" not in task and "metadata" not in task
    response = client.get(task["speech"]["audio_url"])
    assert response.status_code == 200 and response.content[:4] == b"RIFF"
    partial = client.get(task["speech"]["audio_url"], headers={"Range": "bytes=0-43"})
    assert partial.status_code == 206 and len(partial.content) == 44
    assert submit(client, task).status_code == 200
    assert submit(client, task).status_code == 409
    reopened = client.post("/api/undo", json={"annotator": "api-test"}).json()["task"]
    assert reopened["speech"]["latest_annotation"]["corrected_transcript"] == "你好。"
    pending = client.post("/api/export", json={"batch_id": imported["batch_id"]}).json()
    snapshot = client.get(pending["download_url"]).json()
    assert snapshot["items"][0]["final_label"] is None
    assert submit(client, reopened, "您好。").status_code == 200
    first = client.post("/api/export", json={"batch_id": imported["batch_id"]}).json()
    second = client.post("/api/export", json={"batch_id": imported["batch_id"]}).json()
    assert first["sha256"] == second["sha256"]
    snapshot = client.get(first["download_url"]).json()
    item = snapshot["items"][0]
    assert item["task"]["asr_text"] == row["asr_text"]
    assert len(item["annotations"]) == 2
    assert item["final_label"]["corrected_transcript"] == "您好。"
    app.state.db.conn.close()
    restored = create_app(db_path=str(tmp_path / "speech.db"))
    again = TestClient(restored)
    assert again.post("/api/claim", json={"annotator": "api-test"}).json()["task"] is None
    assert restored.state.db.one("SELECT COUNT(*) n FROM speech_annotations")["n"] == 2


def test_speech_type_guards_and_validation(tmp_path):
    _app, client, imported, _row = setup(tmp_path)
    task = claim(client)
    legacy = {"annotator": "api-test", "assignment_id": task["assignment_id"],
              "error_types": ["no_error"], "worst_severity": "neutral", "adequacy": 5, "fluency": 5}
    assert client.post("/api/submit", json=legacy).status_code == 409
    assert submit(client, task, active_ms=1001).status_code == 422
    assert submit(client, task, uncertain_spans=[{"start": 0, "end": 2, "reason": "unclear"}]).status_code == 409
    assert submit(client, task, annotator="someone-else").status_code == 404
    assert client.post("/api/judge/run", json={"batch_id": imported["batch_id"]}).status_code == 409
    assert client.post("/api/probe/build", json={"source_batch_id": imported["batch_id"]}).status_code == 409
    assert client.get("/api/audio/unknown").status_code == 404
    assert client.get("/api/exports/unknown").status_code == 404


def test_audio_tamper_and_missing_assets_are_explicit(tmp_path):
    _app, client, _imported, _row = setup(tmp_path)
    task = claim(client)
    audio = tmp_path / "fixture.wav"
    audio.write_bytes(audio.read_bytes() + b"tampered")
    assert client.get(task["speech"]["audio_url"]).status_code == 409
    audio.unlink()
    assert client.get(task["speech"]["audio_url"]).status_code == 404


def test_serves_speech_player_without_model_service(tmp_path):
    _app, client, _imported, _row = setup(tmp_path)
    assert 'src="speech.js"' in client.get("/").text
    assert "speech-player" in client.get("/speech.js").text
    assert submit(client, claim(client), "").status_code == 200
    assert client.get("/api/stats/overview").json()["n_speech_annotations"] == 1
