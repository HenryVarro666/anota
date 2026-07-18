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

def test_openai_judge_unreachable_raises():
    j = OpenAIJudge("http://127.0.0.1:1", model="m", timeout=0.2)
    with pytest.raises(JudgeUnavailable):
        j.evaluate({"id": "t1", "source": "s", "hypothesis": "h"}, [])

def test_get_judge_default_mock(monkeypatch):
    monkeypatch.delenv("PROPIOQA_JUDGE", raising=False)
    assert get_judge().is_mock is True
