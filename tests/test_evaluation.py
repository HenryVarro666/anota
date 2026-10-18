import json

import httpx
import pytest

from app.evaluation import quantile, summarize
from app.judge import JudgeUnavailable, OpenAIJudge

VALID = {"error_types": ["no_error"], "worst_severity": "neutral", "adequacy": 5,
         "confidence": 0.9, "rationale": "Meaning preserved."}


def test_trace_uses_actual_adapter_and_does_not_include_credentials(monkeypatch):
    seen = []
    def post(url, **kwargs):
        assert kwargs["json"]["max_tokens"] == 256
        assert kwargs["json"]["response_format"]["json_schema"]["strict"]
        assert kwargs["headers"]["Authorization"] == "Bearer secret-for-test"
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(VALID)}}],
            "usage": {"completion_tokens": 32}})
    monkeypatch.setattr(httpx, "post", post)
    judge = OpenAIJudge("http://localhost:1234/v1", model="test", api_key="secret-for-test",
                        max_tokens=256, structured_output=True, observer=seen.append)
    assert judge.evaluate({"id": "t1", "source": "a", "hypothesis": "b"}, []) == VALID
    assert seen[0]["response"]["usage"]["completion_tokens"] == 32
    assert seen[0]["elapsed_seconds"] >= 0 and seen[0]["http_status"] == 200
    assert "secret-for-test" not in json.dumps(seen)


def test_truncation_is_rejected_even_if_content_parses(monkeypatch):
    seen = []
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: httpx.Response(
        200, request=httpx.Request("POST", url),
        json={"choices": [{"finish_reason": "length", "message": {"content": json.dumps(VALID)}}]}))
    judge = OpenAIJudge("http://localhost:1234/v1", model="test", observer=seen.append)
    with pytest.raises(JudgeUnavailable, match="token limit"):
        judge.evaluate({"id": "t1", "source": "a", "hypothesis": "b"}, [])
    assert seen[0]["error"] and seen[0]["response"]


def test_failed_attempts_count_against_quality_and_repeats_are_not_new_samples():
    examples = [{"id": "clean", "parent_id": "p1", "gold": VALID},
                {"id": "critical", "parent_id": "p1", "gold": {
                    "error_types": ["number_unit"], "worst_severity": "critical"}}]
    records = []
    for repeat in (1, 2):
        for item in examples:
            records.append({"phase": "timed", "repeat": repeat, "task_id": item["id"],
                            "verdict": VALID if item["id"] == "clean" else None,
                            "trace": {"elapsed_seconds": 2, "response": None}})
    records.append({"phase": "warmup", "repeat": 0, "task_id": "warmup", "verdict": VALID,
                    "trace": {"elapsed_seconds": 100, "response": None}})
    result = summarize(records, examples, 2)
    assert result["unique_dataset_examples"] == 2 and result["unique_parent_templates"] == 1
    assert result["completed_attempts"] == 4 and result["complete"]
    assert result["passes"]["1"]["valid_output"]["rate"] == 0.5
    assert result["passes"]["1"]["critical_severity_recall"]["rate"] == 0
    assert result["pooled_timing"]["p50"] == 2
    assert result["repeat_label_agreement_valid_only"]["denominator"] == 1


def test_latency_quantile_uses_all_observed_values():
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    assert quantile([1, 2, 3, 4], 0.95) == pytest.approx(3.85)
    assert quantile([], 0.95) is None
