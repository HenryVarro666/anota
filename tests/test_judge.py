import json

import httpx
import pytest
from app.judge import MockJudge, OpenAIJudge, JudgeUnavailable, parse_judge_json, get_judge

LF_CLEAN = [{"lf": n, "label": "OK", "evidence": ""} for n in
            ["lf_negation_drop", "lf_number_mismatch", "lf_untranslated_fragment", "lf_length_ratio"]]
LF_NEG = [{"lf": "lf_negation_drop", "label": "ERROR", "evidence": "no counterpart"}] + LF_CLEAN[1:]

def test_mock_is_deterministic():
    t = {"id": "t001", "source": "s", "hypothesis": "h"}
    assert MockJudge().evaluate(t, LF_NEG) == MockJudge().evaluate(t, LF_NEG)

def test_mock_negation_maps_to_critical():
    v = MockJudge().evaluate({"id": "t002"}, LF_NEG)
    assert v["error_types"] == ["negation_polarity"] and v["worst_severity"] == "critical"
    assert v["adequacy"] < 5 and 0 < v["confidence"] < 1

def test_mock_clean_is_no_error():
    v = MockJudge().evaluate({"id": "t003"}, LF_CLEAN)
    assert v["error_types"] == ["no_error"] and v["worst_severity"] == "neutral"
    assert v["confidence"] > 0.8

def test_parse_judge_json_extracts_and_clamps():
    content = 'Reasoning first. {"rationale":"x","error_types":["omission","bogus"],' \
              '"worst_severity":"major","adequacy":9,"confidence":1.4}'
    v = parse_judge_json(content)
    assert v["error_types"] == ["omission"]      # unknown types dropped
    assert v["adequacy"] == 5 and v["confidence"] == 1.0

def test_parse_judge_json_garbage_raises():
    with pytest.raises(JudgeUnavailable):
        parse_judge_json("no json here")

def test_parse_judge_json_ignores_braces_in_reasoning():
    content = 'I checked {omission, mistranslation} first. Verdict: ' \
              '{"rationale":"r","error_types":["omission"],' \
              '"worst_severity":"major","adequacy":2,"confidence":0.8}'
    v = parse_judge_json(content)
    assert v["error_types"] == ["omission"]

def test_openai_judge_unreachable_raises():
    j = OpenAIJudge("http://127.0.0.1:1", model="m", timeout=0.2)
    with pytest.raises(JudgeUnavailable):
        j.evaluate({"id": "t1", "source": "s", "hypothesis": "h"}, [])

def test_openai_judge_malformed_200_raises_unavailable(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            raise json.JSONDecodeError("bad", "", 0)

    def fake_post(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    j = OpenAIJudge("http://127.0.0.1:1", model="m")
    with pytest.raises(JudgeUnavailable):
        j.evaluate({"id": "t1", "source": "s", "hypothesis": "h"}, [])

def test_get_judge_default_mock(monkeypatch):
    monkeypatch.delenv("ANOTA_JUDGE", raising=False)
    assert get_judge().is_mock is True


def test_aggregate_samples_majority_and_tie():
    from app.judge import aggregate_samples
    s = lambda types, sev, adq: {"error_types": types, "worst_severity": sev,
                                 "adequacy": adq, "rationale": "r", "confidence": 0.8}
    out = aggregate_samples([s(["omission"], "major", 2),
                            s(["omission", "grammar"], "critical", 3),
                            s(["omission"], "major", 2)])
    assert out["error_types"] == ["omission"]          # grammar only 1/3 -> dropped
    assert out["worst_severity"] == "major"            # 2/3 majority
    assert out["adequacy"] == 2 and out["confidence"] == pytest.approx(2 / 3, abs=1e-3)
    tie = aggregate_samples([s(["omission"], "major", 3), s(["omission"], "critical", 3)])
    assert tie["worst_severity"] == "critical"         # tie -> stricter


def test_aggregate_samples_clean_consensus():
    from app.judge import aggregate_samples
    s = lambda: {"error_types": ["no_error"], "worst_severity": "neutral",
                 "adequacy": 5, "rationale": "r", "confidence": 0.9}
    out = aggregate_samples([s(), s(), s()])
    assert out["error_types"] == ["no_error"] and out["worst_severity"] == "neutral"
    assert out["confidence"] == 1.0
