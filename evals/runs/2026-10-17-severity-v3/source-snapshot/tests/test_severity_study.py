import copy
import json
from pathlib import Path

import pytest

from app.severity_study import severity_summary, validate_sets
from scripts.run_severity_study import load_study, schedule

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "evals/studies/severity-v3.json").read_text())


def fixtures():
    primary = [{"id": str(i), "parent_id": str(i), "source": "new " + str(i),
                "mechanism": "clinical_condition" if i == 0 else "other",
                "reference_status": "provisional_policy_assigned", "gold": {
                    "error_types": ["no_error"] if severity == "neutral" else ["omission"],
                    "worst_severity": severity}}
               for i, severity in enumerate(("critical", "major", "minor", "neutral"))]
    disputed = [{"id": "d", "parent_id": "d", "source": "uncertain"}]
    return primary, disputed


def records(primary, disputed):
    result = []
    for phase, repeat, arm, e in schedule(primary, disputed, [], 92):
        if phase == "warmup":
            continue
        result.append({"phase": phase, "repeat": repeat, "arm": arm, "task_id": e["id"],
                       "verdict": copy.deepcopy(e.get("gold", {"error_types": ["omission"], "worst_severity": "critical"})),
                       "trace": {"elapsed_seconds": 2.0, "response": None}})
    return result


def test_disputed_predictions_never_enter_primary_scores():
    primary, disputed = fixtures()
    rows = records(primary, disputed)
    summary = severity_summary(rows, primary, disputed, [], SPEC["gates"])
    assert summary["complete"] and summary["all_pilot_gates_pass"]
    assert summary["disputed_attempts"] == 2
    for arm in ("baseline", "candidate"):
        for p in ("1", "2"):
            scores = summary["arms"][arm]["passes"][p]
            assert scores["attempts"] == 4
            assert scores["critical_severity_recall"]["denominator"] == 1
            assert scores["noncritical_false_critical"]["denominator"] == 2
    validate_sets(primary, disputed, [], [])
    disputed[0]["gold"] = primary[0]["gold"]
    with pytest.raises(ValueError, match="single gold"):
        validate_sets(primary, disputed, [], [])


@pytest.mark.parametrize("prediction", [None, {"error_types": ["omission"], "worst_severity": "major"}])
def test_unavailable_and_undergraded_conditions_fail_critical_gate(prediction):
    primary, disputed = fixtures()
    rows = records(primary, disputed)
    for row in rows:
        if row["task_id"] == "0" and row["arm"] == "candidate":
            row["verdict"] = prediction
    summary = severity_summary(rows, primary, disputed, [], SPEC["gates"])
    assert not summary["all_pilot_gates_pass"]
    for p in ("1", "2"):
        assert not summary["gates"][p]["critical_recall_100_percent"]
        assert not summary["gates"][p]["condition_recall_100_percent"]
        assert summary["arms"]["candidate"]["passes"][p]["condition_critical_recall"] == {"numerator": 0, "denominator": 1, "rate": 0.0}


def test_all_critical_strategy_fails_overgrading_gate():
    primary, disputed = fixtures()
    rows = records(primary, disputed)
    for row in rows:
        if row["arm"] == "candidate" and row["task_id"] in ("1", "2"):
            row["verdict"]["worst_severity"] = "critical"
    summary = severity_summary(rows, primary, disputed, [], SPEC["gates"])
    assert not summary["all_pilot_gates_pass"]
    for p in ("1", "2"):
        assert summary["gates"][p]["critical_recall_100_percent"]
        assert not summary["gates"][p]["noncritical_false_critical_no_worse"]


def test_new_sources_cannot_overlap_historical_texts():
    primary, disputed = fixtures()
    with pytest.raises(ValueError, match="historical"):
        validate_sets(primary, disputed, [], [{"source": " NEW 0 ", "parent_id": "old"}])


def test_counterbalanced_order_and_single_rubric_ablation():
    spec, profiles, primary, disputed, regression = load_study(ROOT / "evals/studies/severity-v3.json")
    assert profiles["baseline"] == profiles["candidate"]
    plan = schedule(primary, disputed, regression, spec["order_seed"])
    assert len(plan) == 110
    for e in primary:
        arms = [[arm for phase, p, arm, item in plan if phase == "holdout" and p == repeat and item["id"] == e["id"]] for repeat in (1, 2)]
        assert arms[0] == arms[1][::-1] and set(arms[0]) == {"baseline", "candidate"}
    assert {e["id"] for phase, _, _, e in plan if phase == "holdout"}.isdisjoint(e["id"] for e in disputed)


def test_each_model_request_uses_exactly_its_selected_system_rubric(monkeypatch):
    from app.judge import OpenAIJudge, SYSTEM_TEMPLATE
    from app.models import ERROR_TYPES, SEVERITIES
    spec, profiles, _, _, _ = load_study(ROOT / "evals/studies/severity-v3.json")
    captured = []
    class Response:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "rationale": "Same meaning", "error_types": ["no_error"], "worst_severity": "neutral", "adequacy": 5, "confidence": .5})}}]}
    def post(url, **kwargs):
        captured.append(kwargs["json"])
        return Response()
    monkeypatch.setattr("app.judge.httpx.post", post)
    for arm in ("baseline", "candidate"):
        profile = profiles[arm]
        rubric = (ROOT / spec["arms"][arm]["guideline"]).read_text(encoding="utf-8")
        judge = OpenAIJudge(profile["base_url"], model=profile["model"], guideline_text=rubric,
                            prompt_suffix=profile["request"]["prompt_suffix"])
        judge.evaluate({"source": "s", "hypothesis": "h"}, [])
        assert captured[-1]["messages"][0]["content"] == SYSTEM_TEMPLATE.format(guideline=rubric, etypes=ERROR_TYPES, sevs=SEVERITIES)
    assert captured[0]["messages"][1] == captured[1]["messages"][1]
    assert captured[0]["messages"][0] != captured[1]["messages"][0]
