import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app

DATA = Path(__file__).resolve().parent.parent / "data"

@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=":memory:", demo=True, data_dir=DATA,
                     export_dir=str(tmp_path / "exports"))
    return TestClient(app)

SUBMIT_OK = dict(error_types=["no_error"], worst_severity="neutral",
                 adequacy=5, fluency=5, elapsed_ms=1500)

def claim(client, who="chao"):
    return client.post("/api/claim", json={"annotator": who}).json()

def test_claim_clean_batch_has_no_signals(client):
    c = claim(client)
    assert c["task"]["task_id"] == "t001"
    assert "suggestions" not in c["task"] and "lf_flags" not in c["task"]
    assert "is_golden" not in c["task"]
    assert c["progress"] == {"done": 0, "total": 30}

def test_submit_and_validation(client):
    c = claim(client)
    r = client.post("/api/submit", json={**SUBMIT_OK, "annotator": "chao",
                                         "assignment_id": c["task"]["assignment_id"]})
    assert r.status_code == 200
    # critical without note -> 422 from pydantic
    c2 = claim(client)
    bad = {**SUBMIT_OK, "error_types": ["number_unit"], "worst_severity": "critical",
           "annotator": "chao", "assignment_id": c2["task"]["assignment_id"]}
    assert client.post("/api/submit", json=bad).status_code == 422
    # double submit -> 409
    ok = {**SUBMIT_OK, "annotator": "chao", "assignment_id": c["task"]["assignment_id"]}
    assert client.post("/api/submit", json=ok).status_code == 409
    # foreign assignment -> 404
    assert client.post("/api/submit", json={**SUBMIT_OK, "annotator": "eve",
                                            "assignment_id": c2["task"]["assignment_id"]}).status_code == 404

def test_undo_reopens(client):
    c = claim(client)
    client.post("/api/submit", json={**SUBMIT_OK, "annotator": "chao",
                                     "assignment_id": c["task"]["assignment_id"]})
    u = client.post("/api/undo", json={"annotator": "chao"}).json()
    assert u["task"]["task_id"] == c["task"]["task_id"]

def test_review_flow(client):
    q = client.get("/api/review/queue").json()
    assert len(q) >= 3                        # maria's seeded annotations
    target = q[0]
    r = client.post(f"/api/review/{target['annotation']['id']}",
                    json={"reviewer": "lead", "verdict": "approved"})
    assert r.status_code == 200
    q2 = client.get("/api/review/queue").json()
    assert all(x["annotation"]["id"] != target["annotation"]["id"] for x in q2)

def test_review_overturn_appends_replacement(client):
    q = client.get("/api/review/queue").json()
    target = q[0]
    rep = dict(error_types=["terminology"], worst_severity="critical", adequacy=2,
               fluency=3, note="term swapped", correction="")
    r = client.post(f"/api/review/{target['annotation']['id']}",
                    json={"reviewer": "lead", "verdict": "overturned",
                          "case_note": "guideline case", "replacement": rep})
    assert r.status_code == 200

def test_stats_endpoints(client):
    m = client.get("/api/stats/matrix").json()
    assert set(m["arms"]) == {"wait1", "wait3", "offline"}
    a = client.get("/api/stats/annotators").json()
    assert any(s["annotator"] == "maria" for s in a)
    o = client.get("/api/stats/overview").json()
    assert o["n_tasks"] == 30 and o["judge"]["mode"] == "mock"
    client.get("/api/stats/agreement").json()   # must not 500

def test_routing_batch_exposes_suggestions(client):
    r = client.post("/api/routing/build", json={"top_n": 5, "signal": "judge_confidence"}).json()
    assert r["n"] == 5
    c = client.post("/api/claim", json={"annotator": "router", "batch_id": r["batch_id"]}).json()
    assert "suggestions" in c["task"]
    assert c["task"]["suggestions"]["judge"]["is_mock"] is True

def test_export_deterministic(client):
    e1 = client.post("/api/export", json={}).json()
    e2 = client.post("/api/export", json={}).json()
    assert e1["sha256"] == e2["sha256"] and e1["version"] != e2["version"]

def test_health_and_guideline(client):
    assert client.get("/api/health").json()["judge"]["mode"] == "mock"
    assert "MQM-lite" in client.get("/api/guideline").json()["text"]
