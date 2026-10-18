"""Queue lifecycle and policy, with controlled predictions (no gold or ASR)."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import speech, speech_queue
from app.main import create_app
from speechqc.schema import transcript_digest
from tests.test_audio_qc_api import qc_manifest, seal


def seed(tmp_path, hints=True, name="queue", app=None):
    app = app or create_app(db_path=str(tmp_path / "queue.db"))
    path, original = qc_manifest(tmp_path)
    rows = []
    for item_id in ("a", "b", "c"):
        row = copy.deepcopy(original)
        row.update(item_id=item_id, source_id=item_id)
        if item_id == "b":
            row["words"] = [{"w": row["asr_text"], "start": 0, "end": 1, "probability": .8}]
        if item_id != "a":
            row["audio_analysis"]["issues"] = []
        row["audio_analysis"]["transcript_sha256"] = transcript_digest(row)
        seal(row["audio_analysis"])
        rows.append(row)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    result = speech.import_speech_manifest(app.state.db, path, name, show_suggestions=hints)
    assert result["n"] == 3, result
    ids = {json.loads(row["provenance"])["item_id"]: row["task_id"] for row in app.state.db.query(
        "SELECT s.* FROM speech_tasks s JOIN tasks t ON t.id=s.task_id WHERE t.batch_id=?", (result["batch_id"],))}
    return app, TestClient(app), result["batch_id"], ids


def fake_scores(rows):
    return [{"status": "ok", "arm": "confidence" if row.get("words") else "legacy",
             "density": {"a": .1, "b": .9, "c": .4}[row["item_id"]], "reason": None,
             "model_id": "fixed-test-model", "bundle_fingerprint": "fixed-test-bundle",
             "feature_schema": "fixed-test-schema"} for row in rows]


@pytest.fixture(autouse=True)
def scoring(monkeypatch):
    monkeypatch.setattr(speech_queue, "_score_rows", fake_scores)


def queue(client, bid, **changes):
    return client.get("/api/speech/review/queue", params={"annotator": "tester", "batch_id": bid, **changes})


def claim(client, bid, **changes):
    return client.post("/api/claim", json={"annotator": "tester", "batch_id": bid, **changes})


def test_preview_and_claim_same_order_switch_and_lease_resume(tmp_path):
    app, client, bid, ids = seed(tmp_path)
    before = app.state.db.one("SELECT COUNT(*) n FROM assignments")["n"]
    ml = queue(client, bid).json()
    assert [row["task_id"] for row in ml["items"]] == [ids[k] for k in ("b", "c", "a")]
    assert ml["counts"]["ml_confidence"] == 1 and ml["counts"]["ml_legacy"] == 2
    assert app.state.db.one("SELECT COUNT(*) n FROM assignments")["n"] == before
    assert queue(client, bid, sort="qc").json()["items"][0]["task_id"] == ids["a"]
    assert [row["task_id"] for row in queue(client, bid, sort="fifo").json()["items"]] == list(ids.values())
    current = claim(client, bid).json()["task"]
    assert current["task_id"] == ids["b"]
    audit = json.loads(app.state.db.one("SELECT payload FROM audit_log WHERE action='claim' ORDER BY id DESC LIMIT 1")["payload"])
    assert audit["effective_method"] == "ml" and audit["selection"] == "next"
    assert audit["ranking"]["arm"] == "confidence" and audit["ranking"]["bundle_fingerprint"] == "fixed-test-bundle"
    assert claim(client, bid, speech_sort="qc").json()["task"]["assignment_id"] == current["assignment_id"]
    assert ids["b"] not in [row["task_id"] for row in queue(client, bid).json()["items"]]
    assert client.post("/api/skip", json={"annotator": "tester", "assignment_id": current["assignment_id"]}).status_code == 200
    assert claim(client, bid).json()["task"]["task_id"] == ids["c"]


def test_unavailable_model_falls_back_entire_queue_to_qc(tmp_path, monkeypatch):
    _app, client, bid, ids = seed(tmp_path)
    def unavailable(rows):
        result = fake_scores(rows)
        result[-1] = {"status": "unavailable", "reason": "bundle_missing"}
        return result
    monkeypatch.setattr(speech_queue, "_score_rows", unavailable)
    result = queue(client, bid).json()
    assert result["items"][0]["task_id"] == ids["a"]
    assert all(row["ranking"]["method"] == "qc" and row["ranking"]["density"] is None
               and row["ranking"]["reason"] == "bundle_missing" for row in result["items"])
    assert claim(client, bid).json()["task"]["task_id"] == ids["a"]


def test_blind_queue_original_order_and_no_inference_or_evidence(tmp_path, monkeypatch):
    _app, client, bid, ids = seed(tmp_path, hints=False)
    def forbidden(_rows):
        pytest.fail("blind batch must not run inference")
    monkeypatch.setattr(speech_queue, "_score_rows", forbidden)
    for sort in ("ml", "qc", "fifo"):
        result = queue(client, bid, sort=sort).json()
        assert [row["task_id"] for row in result["items"]] == list(ids.values())
        assert all("ranking" not in row for row in result["items"])
        assert result["counts"]["blind"] == 3
    payload = claim(client, bid).json()["task"]
    assert payload["task_id"] == ids["a"] and "audio_analysis" not in payload["speech"]


def test_queue_and_claim_no_provenance_gold_or_confidence_leaks(tmp_path):
    _app, client, bid, _ids = seed(tmp_path)
    response = queue(client, bid)
    claimed = claim(client, bid)
    for text in (response.text, claimed.text):
        assert all(secret not in text for secret in (
            "SECRET_REFERENCE", "audio_path", "reference_id", "avg_logprob", "probability", "golden"))
    assert "ranking" not in claimed.json()["task"]


def test_target_is_eligible_and_other_open_lease_cannot_be_replaced(tmp_path):
    app, client, bid, ids = seed(tmp_path)
    targeted = claim(client, bid, task_id=ids["c"]).json()["task"]
    assert targeted["task_id"] == ids["c"]
    audit = json.loads(app.state.db.one("SELECT payload FROM audit_log WHERE action='claim' ORDER BY id DESC LIMIT 1")["payload"])
    assert audit["selection"] == "targeted" and audit["ranking"]["arm"] == "legacy"
    assert claim(client, bid, task_id=ids["c"]).json()["task"]["assignment_id"] == targeted["assignment_id"]
    assert claim(client, bid, task_id=ids["a"]).status_code == 409
    assert app.state.db.one("SELECT COUNT(*) n FROM assignments")["n"] == 1
    assert client.post("/api/claim", json={"annotator": "new", "task_id": ids["a"]}).status_code == 409
    assert claim(client, bid + 100, task_id=ids["a"], annotator="new").status_code == 409
    assert claim(client, bid, task_id="nonexistent", annotator="new").status_code == 409


def test_overlap_and_concurrent_target_claims_keep_single_lease(tmp_path):
    app, client, bid, ids = seed(tmp_path)
    def attempt(annotator):
        return claim(client, bid, task_id=ids["a"], annotator=annotator).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ("one", "two"))) == [200, 409]
    winner = app.state.db.one("SELECT annotator FROM assignments")["annotator"]
    loser = "one" if winner == "two" else "two"
    assert ids["a"] not in [r["task_id"] for r in queue(client, bid, annotator=loser).json()["items"]]


def test_expired_lease_reappears_and_undo_still_append_only(tmp_path):
    app, client, bid, ids = seed(tmp_path)
    current = claim(client, bid).json()["task"]
    app.state.db.execute("UPDATE assignments SET lease_expires_at='2000-01-01' WHERE id=?", (current["assignment_id"],))
    assert queue(client, bid).json()["items"][0]["task_id"] == ids["b"]
    current = claim(client, bid).json()["task"]
    body = {"annotator": "tester", "assignment_id": current["assignment_id"],
            "corrected_transcript": "automated fixture edit", "elapsed_ms": 1, "active_ms": 1}
    assert client.post("/api/speech/submit", json=body).status_code == 200
    assert claim(client, bid).json()["task"]["task_id"] == ids["c"]
    reopened = client.post("/api/undo", json={"annotator": "tester"}).json()["task"]
    assert reopened["task_id"] == ids["b"]
    assert reopened["speech"]["latest_annotation"]["corrected_transcript"] == body["corrected_transcript"]
    assert app.state.db.one("SELECT COUNT(*) n FROM speech_annotations")["n"] == 1


def test_partial_analysis_first_and_translation_original_slots(tmp_path):
    app = create_app(db_path=str(tmp_path / "queue.db"))
    bid = app.state.db.execute("INSERT INTO batches(name) VALUES('translation')")
    app.state.db.execute("INSERT INTO tasks(id,batch_id,source,hypothesis) VALUES('translation',?,'s','h')", (bid,))
    app, client, speech_bid, ids = seed(tmp_path, app=app)
    assert client.post("/api/claim", json={"annotator": "mixed"}).json()["task"]["task_id"] == "translation"
    # Imported legacy clips can have no analysis. They stay visible first as in v1.
    record = app.state.db.one("SELECT provenance FROM speech_tasks WHERE task_id=?", (ids["a"],))
    provenance = json.loads(record["provenance"])
    provenance.pop("audio_analysis")
    app.state.db.execute("UPDATE speech_tasks SET provenance=? WHERE task_id=?", (json.dumps(provenance), ids["a"]))
    assert queue(client, speech_bid).json()["items"][0]["task_id"] == ids["a"]


@pytest.mark.parametrize("changes", [{"sort": "unknown"}, {"limit": 0}, {"limit": 1001}, {"annotator": ""}])
def test_queue_parameters_rejected(tmp_path, changes):
    _app, client, bid, _ids = seed(tmp_path)
    assert queue(client, bid, **changes).status_code == 422


def test_limit_is_after_ranking_and_invalid_claim_sort_rejected(tmp_path):
    _app, client, bid, ids = seed(tmp_path)
    result = queue(client, bid, limit=1).json()
    assert result["total"] == 3 and result["items"][0]["task_id"] == ids["b"]
    assert claim(client, bid, speech_sort="unknown").status_code == 422
