"""Synthetic protocol checks; no real benchmark labels or ASR are read."""

import json
import math
import random
from copy import deepcopy
from hashlib import sha256

import pytest

from scripts.qc_ranking_metrics import (
    DEFAULT_CONFIG,
    METHODS,
    TIE_SALT,
    build_selection_plan,
    evaluate_rankings,
    priority_score,
    rank_rows,
    select_prefix,
    selection_metrics,
)


def row(item_id, duration=1.0, errors=0, *, split="clean", speaker="speaker", **changes):
    value = {
        "item_id": item_id, "speaker_id": speaker, "source_split": split,
        "audio_duration_s": duration, "asr_status": "succeeded", "qc_status": "complete",
        "qc_coverage_status": "ok", "issues": [], "errors": errors,
        "reference_units": max(10, errors), "substitutions": errors, "deletions": 0, "insertions": 0,
    }
    value.update(changes)
    return value


def issue(start, end, priority="medium", code="low_level"):
    return {"start": start, "end": end, "priority": priority, "code": code}


def config(**changes):
    return {**DEFAULT_CONFIG, "random_repetitions": 7, **changes}


def test_score_integrates_max_priority_not_sum_or_issue_count():
    issues = [issue(0, 8, "low"), issue(2, 5, "high"), issue(4, 9, "medium"), issue(2, 5, "high")]
    # 0..2: low, 2..5: high, 5..9: medium, 9..10: none.
    assert priority_score(issues, 10) == pytest.approx(1.9)
    assert priority_score([], 10) == 0
    assert priority_score([issue(0, 10, "high")] * 20, 10) == 3
    assert priority_score([issue(3, 3, "high")], 10) == 0


def test_score_filters_ablation_codes_and_handles_shared_boundaries():
    issues = [issue(0, 2, "high"), issue(2, 4, "medium", "speech_without_transcript")]
    assert priority_score(issues, 4) == 2.5
    assert priority_score(issues, 4, {"low_level"}) == 1.5
    assert priority_score(issues, 4, set()) == 0


def test_score_accepts_only_microsecond_serialization_rounding_at_clip_end():
    duration = 16003 / 16000  # 1.0001875 seconds; six-decimal end rounds upward.
    serialized_end = round(duration, 6)
    assert serialized_end > duration
    assert priority_score([issue(0, serialized_end, "high")], duration) == 3
    # A serialized zero-width interval just beyond duration clamps to empty.
    assert priority_score([issue(serialized_end, serialized_end)], duration) == 0
    with pytest.raises(ValueError):
        priority_score([issue(0, duration + 1.1e-6)], duration)
    with pytest.raises(ValueError):
        priority_score([issue(duration + 1.1e-6, duration + 1.1e-6)], duration)
    with pytest.raises(ValueError):
        priority_score([issue(-0.5e-6, duration)], duration)


@pytest.mark.parametrize("duration", [0, -1, math.inf, math.nan, True, "1"])
def test_score_rejects_invalid_durations(duration):
    with pytest.raises(ValueError):
        priority_score([], duration)


@pytest.mark.parametrize("finding", [issue(-1, 1), issue(1, 0), issue(0, 2), issue(0, 1, "urgent"), issue(0, math.nan)])
def test_score_rejects_invalid_findings(finding):
    with pytest.raises(ValueError):
        priority_score([finding], 1)


class FeaturesOnly(dict):
    def __getitem__(self, key):
        if key in {"errors", "reference_units", "substitutions", "deletions", "insertions", "gold", "reference_text"}:
            raise AssertionError(f"gold access during ranking: {key}")
        return super().__getitem__(key)


def test_ranking_does_not_read_gold_and_is_invariant_to_gold_changes():
    rows = [row("a", issues=[issue(0, 0.5)]), row("b", errors=8), row("c", duration=2)]
    changed = deepcopy(rows)
    for value in changed:
        value.update(errors=9999, reference_units=12345, substitutions=9999)
    for method in METHODS:
        expected = [r["item_id"] for r in rank_rows(rows, method)]
        assert [r["item_id"] for r in rank_rows([FeaturesOnly(r) for r in rows], method)] == expected
        assert [r["item_id"] for r in rank_rows(changed, method)] == expected


def test_failure_tiers_precede_scores_and_baselines_ignore_tiers():
    rows = [
        row("complete", duration=1, issues=[issue(0, 1, "high")]),
        row("unavailable", duration=2, qc_coverage_status="unavailable"),
        row("partial", duration=3, qc_status="partial", issues=[issue(0, 3, "low")]),
        row("failed", duration=4, asr_status="failed"),
    ]
    assert [r["item_id"] for r in rank_rows(rows)] == ["failed", "partial", "unavailable", "complete"]
    assert [r["item_id"] for r in rank_rows(rows, "shortest")] == ["complete", "unavailable", "partial", "failed"]
    assert [r["item_id"] for r in rank_rows(rows, "longest")] == ["failed", "partial", "unavailable", "complete"]


def test_ablation_order_and_hash_ties_are_frozen():
    rows = [row("coverage", issues=[issue(0, 1, "high", "speech_without_transcript")]),
            row("dsp", issues=[issue(0, 1, "medium")])]
    assert rank_rows(rows, "dsp_only")[0]["item_id"] == "dsp"
    assert rank_rows(rows, "coverage_only")[0]["item_id"] == "coverage"
    ties = [row(name) for name in ("c", "b", "a")]
    expected = sorted((r["item_id"] for r in ties), key=lambda name: sha256((TIE_SALT + name).encode()).hexdigest())
    assert [r["item_id"] for r in rank_rows(ties)] == expected
    assert [r["item_id"] for r in rank_rows(list(reversed(ties)))] == expected


def test_exact_integer_microsecond_budget_and_first_over_budget_stop():
    rows = [row("a", duration=0.1), row("b", duration=0.2), row("c", duration=0.7)]
    selected, target = select_prefix(rows, 30)
    assert target == 300_000
    assert [r["item_id"] for r in selected] == ["a", "b"]
    selected, target = select_prefix([rows[2], rows[0], rows[1]], 20)
    assert selected == [] and target == 200_000
    assert len(select_prefix(rows, 100)[0]) == 3
    assert select_prefix(rows, 0)[0] == []
    rounded = [row("x", duration=0.0000016), row("y", duration=0.0000016), row("z", duration=0.0000016)]
    selected, target = select_prefix(rounded, 50)
    assert target == 3 and len(selected) == 1


def test_capture_residual_and_failure_metrics_have_explicit_denominators():
    rows = [row("a", duration=2, errors=4, asr_status="failed", qc_status="partial"),
            row("b", duration=3, errors=1), row("c", duration=5)]
    metrics = selection_metrics(rows, rows[:1], 2_000_000)
    assert metrics == {
        "selected_count": 1, "target_audio_s": 2.0, "selected_audio_s": 2.0,
        "unused_budget_s": 0.0, "captured_reference_units": 10, "selected_wer": 0.4,
        "actual_audio_fraction": 0.2, "captured_errors": 4, "error_capture_rate": 0.8,
        "captured_error_clips": 1, "error_clip_recall": 0.5, "residual_errors": 1,
        "residual_reference_units": 20, "residual_wer": 0.05,
        "captured_asr_failure_errors": 4, "qc_unavailable_selected_count": 1,
    }
    corrupted_copy = dict(rows[0], errors=999)
    assert selection_metrics(rows, [corrupted_copy], 2_000_000) == metrics


def test_random_reproducible_same_order_across_budgets_and_input_order_independent():
    rows = [row(str(i), duration=(i % 3) + 1, errors=i) for i in range(8)]
    cfg = config(budgets_percent=[10, 20, 50, 100])
    result = evaluate_rankings(rows, cfg)
    assert evaluate_rankings(list(reversed(rows)), cfg) == result
    assert len(result["random_trials"]) == 2 * 7 * 4
    for seed in range(cfg["random_seed"], cfg["random_seed"] + 7):
        ordered = sorted(rows, key=lambda r: r["item_id"])
        random.Random(seed).shuffle(ordered)
        for budget in cfg["budgets_percent"]:
            selected, target = select_prefix(ordered, budget)
            expected = selection_metrics(rows, selected, target)
            trial = next(t for t in result["random_trials"] if t["group"] == "pooled" and t["seed"] == seed and t["budget_percent"] == budget)
            assert trial == {"group": "pooled", "seed": seed, "budget_percent": budget, **expected}


def test_split_recomputes_own_ranking_and_budget_and_totals_sum():
    rows = [row("a", duration=1, errors=1, split="clean", issues=[issue(0, 1, "high")]),
            row("b", duration=3, errors=2, split="clean"),
            row("c", duration=6, errors=3, split="other"), row("d", duration=10, split="other")]
    result = evaluate_rankings(rows, config(budgets_percent=[10, 20, 50, 100]))
    assert set(result["groups"]) == {"pooled", "split:clean", "split:other"}
    groups = result["groups"]
    assert groups["pooled"]["totals"]["errors"] == sum(groups[name]["totals"]["errors"] for name in ("split:clean", "split:other"))
    assert groups["split:clean"]["methods"]["qc"]["20"]["selected_count"] == 0
    assert groups["pooled"]["methods"]["qc"]["20"]["selected_count"] >= 1
    for name in groups:
        if name == "pooled":
            subset = rows
        else:
            subset = [r for r in rows if r["source_split"] == name.removeprefix("split:")]
        for method in METHODS:
            for budget in (10, 20, 50, 100):
                selected, target = select_prefix(rank_rows(subset, method), budget)
                assert groups[name]["methods"][method][str(budget)] == selection_metrics(subset, selected, target)


def test_all_zero_errors_and_empty_corpus_are_defined_without_division_by_zero():
    for rows in ([], [row("ok")]):
        result = evaluate_rankings(rows, config(budgets_percent=[10, 100]))
        group = result["groups"]["pooled"]
        for budget in ("10", "100"):
            metrics = group["methods"]["qc"][budget]
            assert metrics["error_capture_rate"] is None
            assert metrics["error_clip_recall"] is None
            assert group["random"][budget]["error_capture_rate"] == {
                "mean": None, "median": None, "p2_5": None, "p97_5": None, "defined_count": 0,
            }
        assert group["methods"]["qc"]["100"]["residual_wer"] is None


def test_random_aggregation_matches_trials_and_quantile_interpolation():
    rows = [row(str(i), errors=i) for i in range(5)]
    result = evaluate_rankings(rows, config(budgets_percent=[20], random_repetitions=5))
    values = sorted(t["captured_errors"] for t in result["random_trials"] if t["group"] == "pooled")
    summary = result["groups"]["pooled"]["random"]["20"]["captured_errors"]
    assert summary["mean"] == sum(values) / 5
    assert summary["median"] == values[2]
    assert summary["p2_5"] == pytest.approx(values[0] + 0.1 * (values[1] - values[0]))
    assert summary["p97_5"] == pytest.approx(values[3] + 0.9 * (values[4] - values[3]))


@pytest.mark.parametrize("changes", [
    {"audio_duration_s": 0}, {"audio_duration_s": -1}, {"audio_duration_s": math.inf},
    {"audio_duration_s": 0.00000001}, {"errors": -1}, {"errors": 1.2},
    {"errors": True}, {"errors": 1, "substitutions": 0}, {"errors": 11, "substitutions": 11, "reference_units": 10},
    {"reference_units": -1}, {"speaker_id": ""}, {"source_split": ""},
])
def test_invalid_rows_rejected(changes):
    with pytest.raises(ValueError):
        evaluate_rankings([row("invalid", **changes)], config())


def test_duplicate_unknown_selected_and_overbudget_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        rank_rows([row("duplicate"), row("duplicate")])
    with pytest.raises(ValueError, match="duplicate"):
        selection_metrics([row("a")], [row("a"), row("a")], 1_000_000)
    with pytest.raises(ValueError, match="absent"):
        selection_metrics([row("a")], [row("b")], 1_000_000)
    with pytest.raises(ValueError, match="exceeds"):
        selection_metrics([row("a")], [row("a")], 100_000)
    with pytest.raises(ValueError, match="exceeds"):
        selection_metrics([row("a")], [], 2_000_000)


@pytest.mark.parametrize("changes", [
    {"random_repetitions": 0}, {"random_repetitions": -1}, {"random_seed": -1},
    {"tie_salt": "tuned"}, {"budgets_percent": []}, {"budgets_percent": [10, 10]},
    {"budgets_percent": [10.5]}, {"budgets_percent": [101]}, {"unknown": True},
])
def test_invalid_config_rejected(changes):
    with pytest.raises(ValueError):
        evaluate_rankings([row("a")], config(**changes))


def test_input_rows_are_not_mutated():
    rows = [row("a", issues=[issue(0, 1)]), row("b", errors=5)]
    before = deepcopy(rows)
    evaluate_rankings(rows, config())
    assert rows == before


def test_selection_plan_never_reads_gold_and_is_input_order_invariant():
    rows = [row("a", issues=[issue(0, 1)]), row("b", duration=2, errors=5),
            row("c", duration=3, split="other", asr_status="failed")]
    planned = build_selection_plan([FeaturesOnly(value) for value in rows], config())
    assert build_selection_plan(list(reversed(rows)), config()) == planned
    # The planning contract needs no reference/error count/speaker fields at all.
    features = [{key: value for key, value in original.items() if key not in {
        "errors", "reference_units", "substitutions", "deletions", "insertions", "speaker_id",
    }} for original in rows]
    assert build_selection_plan(features, config()) == planned


def test_selection_plan_matches_evaluation_choices_and_random_order_hashes():
    rows = [row(str(i), duration=(i % 4) + 0.1234567, errors=i,
                split="clean" if i % 2 else "other") for i in range(9)]
    cfg = config()
    plan = build_selection_plan(rows, cfg)
    evaluation = evaluate_rankings(rows, cfg)
    for group_name, planned in plan["groups"].items():
        group_rows = rows if group_name == "pooled" else [
            value for value in rows if value["source_split"] == group_name.removeprefix("split:")
        ]
        group_result = evaluation["groups"][group_name]
        assert planned["count"] == len(group_rows)
        assert planned["total_audio_us"] == group_result["totals"]["audio_duration_us"]
        for method in METHODS:
            method_plan = planned["methods"][method]
            assert method_plan["order_ids"] == [value["item_id"] for value in rank_rows(group_rows, method)]
            for budget, choice in method_plan["budgets"].items():
                measured = group_result["methods"][method][budget]
                assert choice["selected_count"] == measured["selected_count"]
                assert choice["target_audio_us"] / 1_000_000 == measured["target_audio_s"]
                assert choice["selected_audio_us"] / 1_000_000 == measured["selected_audio_s"]
        for random_plan in planned["random"]:
            assert "order_ids" not in random_plan
            order = sorted(group_rows, key=lambda value: value["item_id"])
            random.Random(random_plan["seed"]).shuffle(order)
            encoded = json.dumps([value["item_id"] for value in order], sort_keys=True,
                                 separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            assert random_plan["order_sha256"] == sha256(encoded).hexdigest()
            for budget, choice in random_plan["budgets"].items():
                measured = next(trial for trial in evaluation["random_trials"]
                                if trial["group"] == group_name and trial["seed"] == random_plan["seed"]
                                and trial["budget_percent"] == int(budget))
                assert choice["selected_count"] == measured["selected_count"]
                assert choice["target_audio_us"] / 1_000_000 == measured["target_audio_s"]
                assert choice["selected_audio_us"] / 1_000_000 == measured["selected_audio_s"]


def test_empty_selection_plan_and_non_ascii_ids_have_stable_hashes():
    cfg = config(random_repetitions=1)
    empty = build_selection_plan([], cfg)["groups"]["pooled"]
    assert empty["random"][0]["order_sha256"] == sha256(b"[]").hexdigest()
    assert empty["random"][0]["budgets"]["10"] == {
        "selected_count": 0, "target_audio_us": 0, "selected_audio_us": 0,
    }
    unicode_plan = build_selection_plan([row("声")], cfg)["groups"]["pooled"]
    assert unicode_plan["random"][0]["order_sha256"] == sha256('["声"]'.encode()).hexdigest()


@pytest.mark.parametrize("bad_rows", [[row("same"), row("same")], [row("bad", duration=0)],
                                     [row("bad", source_split="")]])
def test_selection_plan_rejects_invalid_features_without_needing_gold(bad_rows):
    with pytest.raises(ValueError):
        build_selection_plan([FeaturesOnly(value) for value in bad_rows], config())
