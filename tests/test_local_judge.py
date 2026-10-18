"""Local provider failure contracts; no model, network or GPU required."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.judge import JudgeUnavailable, OpenAIJudge, get_judge, judge_health, parse_judge_json
from app.main import create_app

VALID = dict(error_types=["no_error"], worst_severity="neutral", adequacy=5,
             confidence=0.9, rationale="Numbers and negation are preserved.")


@pytest.mark.parametrize("change", [
    {"error_types": []}, {"error_types": ["unknown"]}, {"error_types": "no_error"},
    {"error_types": ["no_error", "omission"]}, {"error_types": ["no_error", "no_error"]},
    {"error_types": [{"unexpected": "object"}]}, {"worst_severity": "critical"},
    {"worst_severity": "unknown"}, {"adequacy": None}, {"adequacy": True},
    {"adequacy": "5"}, {"adequacy": 6}, {"confidence": float("nan")},
    {"confidence": float("inf")}, {"confidence": -0.1}, {"rationale": None},
])
def test_invalid_verdict_never_becomes_a_clean_label(change):
    with pytest.raises(JudgeUnavailable):
        parse_judge_json(json.dumps({**VALID, **change}))


@pytest.mark.parametrize("content", [None, [], {}, "{}"])
def test_missing_or_nontext_verdict_is_unavailable(content):
    with pytest.raises(JudgeUnavailable):
        parse_judge_json(content)


def test_valid_fenced_verdict_is_accepted():
    assert parse_judge_json("```json\n" + json.dumps(VALID) + "\n```") == VALID


def test_local_timeout_can_be_configured(monkeypatch):
    monkeypatch.setenv("ANOTA_JUDGE", "openai")
    monkeypatch.setenv("ANOTA_JUDGE_TIMEOUT", "120")
    assert get_judge().timeout == 120


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_invalid_timeout_is_rejected(timeout):
    with pytest.raises(ValueError):
        OpenAIJudge("http://localhost:1234/v1", timeout=timeout)


def test_ambiguous_model_list_requires_explicit_selection(monkeypatch):
    def response(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", args[0]),
                              json={"data": [{"id": "embedding"}, {"id": "chat"}]})
    monkeypatch.setattr(httpx, "get", response)
    with pytest.raises(JudgeUnavailable, match="explicitly"):
        OpenAIJudge("http://localhost:1234/v1").evaluate(
            {"source": "a", "hypothesis": "b"}, [])


def test_health_checks_http_status_and_authentication(monkeypatch):
    def response(url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer test-key"
        return httpx.Response(401, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", response)
    assert judge_health(OpenAIJudge("http://localhost:1234/v1", api_key="test-key")) == {
        "mode": "openai", "reachable": False}


def test_failed_provider_batch_finishes_and_preserves_human_work(monkeypatch, tmp_path):
    monkeypatch.setenv("ANOTA_JUDGE", "openai")
    monkeypatch.setenv("ANOTA_JUDGE_MODEL", "local-test")
    monkeypatch.setenv("ANOTA_JUDGE_WORKERS", "1")
    def response(url, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": '{"error_types":["invented"]}'}}]})
    monkeypatch.setattr(httpx, "post", response)
    app = create_app(demo=True, export_dir=str(tmp_path))
    with TestClient(app) as client:
        before = app.state.db.one("SELECT COUNT(*) n FROM judge_results")["n"]
        result = client.post("/api/judge/run", json={"batch_id": 1})
        assert result.status_code == 503
        state = client.get("/api/judge/status").json()["1"]
        assert state["running"] is False and state["done"] == 0
        assert app.state.db.one("SELECT COUNT(*) n FROM judge_results")["n"] == before
        claimed = client.post("/api/claim", json={"annotator": "reviewer"}).json()
        assert claimed["task"] is not None and "suggestions" not in claimed["task"]


@pytest.mark.parametrize("workers", ["0", "65"])
def test_invalid_worker_count_fails_at_startup(monkeypatch, workers):
    monkeypatch.setenv("ANOTA_JUDGE_WORKERS", workers)
    with pytest.raises(ValueError, match="ANOTA_JUDGE_WORKERS"):
        create_app()


def test_local_worker_limit_serializes_provider_requests(monkeypatch, tmp_path):
    import threading
    import time
    from app.judge import MockJudge

    lock = threading.Lock()
    active = peak = 0
    original = MockJudge.evaluate

    def measured(self, task, flags):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.005)
            return original(self, task, flags)
        finally:
            with lock:
                active -= 1

    monkeypatch.setenv("ANOTA_JUDGE", "mock")
    monkeypatch.setenv("ANOTA_JUDGE_WORKERS", "1")
    app = create_app(demo=True, export_dir=str(tmp_path))
    monkeypatch.setattr(MockJudge, "evaluate", measured)
    with TestClient(app) as client:
        response = client.post("/api/judge/run", json={"batch_id": 1})
    assert response.status_code == 200 and response.json()["n"] == 30
    assert peak == 1


def test_unexpected_backend_failure_does_not_leave_batch_busy(monkeypatch, tmp_path):
    from app.judge import MockJudge

    app = create_app(demo=True, export_dir=str(tmp_path))
    def fail(*args):
        raise RuntimeError("synthetic backend failure")
    monkeypatch.setattr(MockJudge, "evaluate", fail)
    with TestClient(app) as client:
        response = client.post("/api/judge/run", json={"batch_id": 1})
        assert response.status_code == 503
        assert client.get("/api/judge/status").json()["1"]["running"] is False
        assert client.post("/api/claim", json={"annotator": "reviewer"}).status_code == 200


def test_worker_budget_is_shared_across_batches(monkeypatch, tmp_path):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from app.judge import MockJudge

    lock = threading.Lock()
    active = peak = 0
    original = MockJudge.evaluate
    def measured(self, task, flags):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.005)
            return original(self, task, flags)
        finally:
            with lock:
                active -= 1
    monkeypatch.setenv("ANOTA_JUDGE", "mock")
    monkeypatch.setenv("ANOTA_JUDGE_WORKERS", "1")
    app = create_app(demo=True, export_dir=str(tmp_path))
    with TestClient(app) as client:
        probe_id = client.post("/api/probe/build", json={}).json()["batch_id"]
        monkeypatch.setattr(MockJudge, "evaluate", measured)
        with ThreadPoolExecutor(max_workers=2) as executor:
            replies = list(executor.map(lambda bid: client.post("/api/judge/run", json={"batch_id": bid}), [1, probe_id]))
        assert all(reply.status_code == 200 for reply in replies)
    assert peak == 1


def test_judge_initialization_failure_releases_batch(monkeypatch, tmp_path):
    import app.main as main_module
    app = create_app(demo=True, export_dir=str(tmp_path))
    def fail(*args):
        raise ValueError("invalid runtime setting")
    monkeypatch.setattr(main_module, "get_judge", fail)
    with TestClient(app) as client:
        response = client.post("/api/judge/run", json={"batch_id": 1})
        assert response.status_code == 503
        assert client.get("/api/judge/status").json()["1"]["running"] is False
        assert client.post("/api/claim", json={"annotator": "reviewer"}).status_code == 200
