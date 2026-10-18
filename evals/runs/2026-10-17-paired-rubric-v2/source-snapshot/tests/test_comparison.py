import copy
import pytest

from app.comparison import comparison_summary
from scripts.compare_judges import schedule_requests, validate_fixtures


def fixtures():
    return [{"id": "a", "parent_id": "a", "source": "New clinical action", "gold": {
                "error_types": ["negation_polarity"], "worst_severity": "critical"}},
            {"id": "b", "parent_id": "b", "source": "New clean control", "gold": {
                "error_types": ["no_error"], "worst_severity": "neutral"}}]


def test_source_and_parent_overlap_are_rejected():
    rows = fixtures()
    validate_fixtures(rows, [{"source": "Other", "parent_id": "different"}])
    with pytest.raises(ValueError, match="source"):
        validate_fixtures(rows, [{"source": " new CLINICAL action ", "parent_id": "different"}])
    with pytest.raises(ValueError, match="parent"):
        validate_fixtures(rows, [{"source": "Other", "parent_id": "a"}])


def test_order_is_counterbalanced_for_every_item_and_repeat():
    rows = fixtures()
    schedule = schedule_requests(rows, [])
    assert len(schedule) == 2 + 4 * len(rows)
    assert schedule == schedule_requests(rows, [])
    for row in rows:
        first = [arm for phase, repeat, arm, item in schedule if phase == "holdout" and repeat == 1 and item["id"] == row["id"]]
        second = [arm for phase, repeat, arm, item in schedule if phase == "holdout" and repeat == 2 and item["id"] == row["id"]]
        assert first == second[::-1] and set(first) == {"baseline", "candidate"}


def records():
    result = []
    for repeat in (1, 2):
        for arm in ("baseline", "candidate"):
            for row in fixtures():
                verdict = copy.deepcopy(row["gold"])
                # Baseline misses clinical error; candidate fails the clean request.
                if arm == "baseline" and row["id"] == "a":
                    verdict = {"error_types": ["no_error"], "worst_severity": "neutral"}
                if arm == "candidate" and row["id"] == "b": verdict = None
                result.append({"phase": "holdout", "arm": arm, "repeat": repeat, "task_id": row["id"],
                               "verdict": verdict, "trace": {"elapsed_seconds": 2, "response": None}})
    return result


def test_paired_wins_losses_include_unavailable_not_only_valid_pairs():
    summary = comparison_summary(records(), fixtures(), [])
    assert summary["complete"] and summary["holdout_unique_items"] == 2
    for repeat in ("1", "2"):
        assert summary["paired"][repeat]["binary"] == {"candidate_wins": 1, "candidate_losses": 1, "ties": 0}
        c = summary["arms"]["candidate"]["passes"][repeat]
        assert c["attempts"] == 2 and c["valid_output"]["rate"] == .5
        assert c["latency_seconds"]["p50"] == 2
        assert not summary["gates"][repeat]["validity_no_worse"]
    assert not summary["all_pilot_gates_pass"]
    assert summary["arms"]["candidate"]["repeat_label_agreement"]["denominator"] == 1


def test_warmup_and_development_do_not_leak_into_holdout_denominators():
    rows = records()
    rows.append(dict(rows[0], phase="warmup", task_id="warmup", repeat=0))
    summary = comparison_summary(rows, fixtures(), [])
    assert summary["completed_measured_requests"] == 8
    assert summary["arms"]["baseline"]["passes"]["1"]["attempts"] == 2
