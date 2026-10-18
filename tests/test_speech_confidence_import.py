"""Real engine confidence survives provenance but stays blind in review payloads."""
import json

import pytest
from fastapi.testclient import TestClient

from app import export, speech
from app.main import create_app
from tests.test_speech_backend import manifest

CONFIDENCE = {"avg_logprob": 0, "no_speech_prob": 0,
              "compression_ratio": 0, "temperature": 0}


def confidence_manifest(tmp_path):
    return manifest(tmp_path,
                    segments=[{"start": 0, "end": 1, "text": "你好", **CONFIDENCE}],
                    words=[{"start": 0, "end": 1, "w": "你好", "probability": 0}])


def test_zero_missing_null_and_real_values_are_preserved(tmp_path):
    path, row = confidence_manifest(tmp_path)
    provenance = speech._normalise_row(row, path.parent)
    assert provenance["words"][0]["probability"] == 0
    for field in CONFIDENCE:
        assert provenance["segments"][0][field] == 0
    row["words"].append({"start": 0, "end": 1, "w": "missing"})
    row["words"].append({"start": 0, "end": 1, "w": "null", "probability": None})
    row["segments"].append({"start": 0, "end": 1, "text": "missing"})
    row["segments"][0].update(avg_logprob=-2.5, no_speech_prob=.25,
                               compression_ratio=3.2, temperature=1.4)
    provenance = speech._normalise_row(row, path.parent)
    assert "probability" not in provenance["words"][1]
    assert provenance["words"][2]["probability"] is None
    assert set(provenance["segments"][1]) == {"start", "end", "text"}
    assert provenance["segments"][0]["avg_logprob"] == -2.5
    assert provenance["segments"][0]["temperature"] == 1.4


@pytest.mark.parametrize("container,field", [("words", "probability")] + [
    ("segments", field) for field in CONFIDENCE])
@pytest.mark.parametrize("value", [True, False, "0.5", float("nan"), float("inf"), -float("inf"), 10 ** 1000])
def test_invalid_confidence_numbers_rejected(tmp_path, container, field, value):
    path, row = confidence_manifest(tmp_path)
    row[container][0][field] = value
    with pytest.raises(ValueError, match=field):
        speech._normalise_row(row, path.parent)


@pytest.mark.parametrize("container,field,value", [
    ("words", "probability", -.01), ("words", "probability", 1.01),
    ("segments", "avg_logprob", .01), ("segments", "no_speech_prob", -.01),
    ("segments", "no_speech_prob", 1.01), ("segments", "compression_ratio", -.01),
    ("segments", "temperature", -.01)])
def test_invalid_confidence_ranges_rejected(tmp_path, container, field, value):
    path, row = confidence_manifest(tmp_path)
    row[container][0][field] = value
    path.write_text(json.dumps(row), encoding="utf-8")
    from app.db import Database
    db = Database(":memory:")
    result = speech.import_speech_manifest(db, path, "invalid")
    assert result["invalid"] == 1 and result["n"] == 0
    assert field in result["errors"][0]["error"]
    db.conn.close()


@pytest.mark.parametrize("hinted", [False, True])
def test_import_export_idempotence_and_claim_undo_blindness(tmp_path, hinted):
    app = create_app(db_path=str(tmp_path / "test.db"))
    client = TestClient(app)
    db = app.state.db
    path, row = confidence_manifest(tmp_path)
    imported = speech.import_speech_manifest(db, path, "confidence", show_suggestions=hinted)
    assert imported["n"] == 1
    row.update(run_id="second-run", processing_ms=0, cache_hit=True)
    path.write_text(json.dumps(row), encoding="utf-8")
    assert speech.import_speech_manifest(db, path, "confidence")["skipped"] == 1
    snapshot = export.build_snapshot(db)["items"][0]["task"]["provenance"]
    assert snapshot["words"][0]["probability"] == 0
    for field in CONFIDENCE:
        assert snapshot["segments"][0][field] == 0
    assert json.loads(db.one("SELECT provenance FROM speech_tasks")["provenance"]) == snapshot
    task = client.post("/api/claim", json={"annotator": "tester"}).json()["task"]
    submitted = client.post("/api/speech/submit", json={
        "annotator": "tester", "assignment_id": task["assignment_id"],
        "corrected_transcript": "你好", "elapsed_ms": 1000, "active_ms": 500})
    assert submitted.status_code == 200
    undone = client.post("/api/undo", json={"annotator": "tester"}).json()["task"]
    for payload in (task, undone):
        assert set(payload["speech"]["words"][0]) == {"start", "end", "w"}
        assert set(payload["speech"]["segments"][0]) == {"start", "end", "text"}
        assert all(field not in json.dumps(payload) for field in ["probability", *CONFIDENCE])
    # Different immutable engine evidence creates another task without rewriting the old one.
    row["words"][0]["probability"] = .9
    path.write_text(json.dumps(row), encoding="utf-8")
    assert speech.import_speech_manifest(db, path, "confidence")["n"] == 1
    assert db.one("SELECT COUNT(*) n FROM speech_tasks")["n"] == 2
    assert export.build_snapshot(db)["items"][0]["task"]["provenance"] == snapshot
    db.conn.close()
