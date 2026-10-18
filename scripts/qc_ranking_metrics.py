"""Gold-isolated ranking and duration-budget metrics for audio QC experiments.

This module uses only the standard library. ``rank_rows`` and ``priority_score``
consume observable QC features, never reference text or error labels. Gold counts
enter only the measurement functions after the order has been determined.
Random percentile ranges describe variation across seeded orders, not sampling
confidence intervals for the dataset or a population.
"""

from __future__ import annotations

import json
import math
import random
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from hashlib import sha256
from statistics import fmean, median
from typing import Any

from speechqc.priority import PRIORITIES, TIE_SALT, priority_score

__all__ = [
    "COVERAGE_CODES", "DEFAULT_CONFIG", "DSP_CODES", "METHODS", "PRIORITIES", "TIE_SALT",
    "build_selection_plan", "evaluate_rankings", "priority_score", "rank_rows",
    "select_prefix", "selection_metrics",
]

DSP_CODES = frozenset({"low_level", "low_energy", "near_clipping", "dc_offset"})
COVERAGE_CODES = frozenset({"speech_without_transcript", "transcript_without_speech"})
METHODS = ("qc", "dsp_only", "coverage_only", "shortest", "longest")
DEFAULT_CONFIG = {
    "budgets_percent": [5, 10, 20, 30, 50, 100],
    "random_repetitions": 1000,
    "random_seed": 20261017,
    "tie_salt": TIE_SALT,
}


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")  # noqa: TRY004 - one data-validation error type
    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{field} must be a finite number")
    return numeric


def _duration_us(row: Mapping[str, Any]) -> int:
    duration = _number(row["audio_duration_s"], "audio_duration_s")
    if duration <= 0 or not math.isfinite(duration * 1_000_000):
        raise ValueError("audio_duration_s must be positive and representable")
    result = round(duration * 1_000_000)
    if result <= 0:
        raise ValueError("audio_duration_s rounds to zero microseconds")
    return result


def _count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _budget(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError("budget percentage must be an integer from 0 to 100")
    return value


def _feature_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    """Validate ranking input without touching any gold-derived field."""
    seen = set()
    for row in rows:
        item_id = row["item_id"]
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("item_id must be a nonempty string")
        if item_id in seen:
            raise ValueError(f"duplicate item_id: {item_id}")
        seen.add(item_id)
        _duration_us(row)


def _failure_tier(row: Mapping[str, Any]) -> int:
    if row["asr_status"] != "succeeded":
        return 2
    if row["qc_status"] != "complete" or row["qc_coverage_status"] != "ok":
        return 1
    return 0


def _qc_unavailable(row: Mapping[str, Any]) -> bool:
    return row["qc_status"] != "complete" or row["qc_coverage_status"] != "ok"


def rank_rows(rows: Sequence[Mapping[str, Any]], method: str = "qc") -> list[Mapping[str, Any]]:
    """Rank observable features only. Return original row objects, without edits.

    QC methods put ASR failures first, unavailable/incomplete QC second, and
    complete cases last. Within each tier, higher priority-duration score comes
    first. Duration baselines have no failure tier. All ties use the fixed hash.
    """
    if method not in METHODS:
        raise ValueError(f"unknown ranking method: {method}")
    _feature_rows(rows)
    allowed_codes = DSP_CODES if method == "dsp_only" else COVERAGE_CODES if method == "coverage_only" else None

    def key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        tie = sha256((TIE_SALT + row["item_id"]).encode("utf-8")).hexdigest()
        if method in ("shortest", "longest"):
            duration = _duration_us(row)
            return (duration if method == "shortest" else -duration, tie)
        return (
            -_failure_tier(row),
            -priority_score(row["issues"], row["audio_duration_s"], allowed_codes),
            tie,
        )

    return sorted(rows, key=key)


def select_prefix(
    ordered_rows: Sequence[Mapping[str, Any]], budget_pct: int,
) -> tuple[list[Mapping[str, Any]], int]:
    """Return (whole-recording prefix, target microseconds); never skip a row.

    Durations are rounded to integer microseconds before summation. The budget
    is floor(total_microseconds * percentage / 100). At the first recording
    that would exceed it, selection stops, even if later recordings would fit.
    """
    _feature_rows(ordered_rows)
    budget_pct = _budget(budget_pct)
    durations = [_duration_us(row) for row in ordered_rows]
    target_us = sum(durations) * budget_pct // 100
    selected = []
    elapsed = 0
    for row, duration in zip(ordered_rows, durations):
        if elapsed + duration > target_us:
            break
        selected.append(row)
        elapsed += duration
    return selected, target_us


def _validate_gold(rows: Sequence[Mapping[str, Any]]) -> None:
    _feature_rows(rows)
    for row in rows:
        for field in ("errors", "reference_units", "substitutions", "deletions", "insertions"):
            _count(row[field], field)
        if row["errors"] != row["substitutions"] + row["deletions"] + row["insertions"]:
            raise ValueError("errors must equal substitutions + deletions + insertions")
        if row["substitutions"] + row["deletions"] > row["reference_units"]:
            raise ValueError("substitutions + deletions cannot exceed reference_units")
        if not isinstance(row["source_split"], str) or not row["source_split"]:
            raise ValueError("source_split must be a nonempty string")
        if not isinstance(row["speaker_id"], str) or not row["speaker_id"]:
            raise ValueError("speaker_id must be a nonempty string")


# Prefix vectors: duration_us, errors, reference units, error clips,
# ASR-failure errors, QC-unavailable clips, substitutions, deletions, insertions.
_ZERO = (0,) * 9


def _row_vector(row: Mapping[str, Any]) -> tuple[int, ...]:
    return (
        _duration_us(row), row["errors"], row["reference_units"], int(row["errors"] > 0),
        row["errors"] if row["asr_status"] != "succeeded" else 0,
        int(_qc_unavailable(row)), row["substitutions"], row["deletions"], row["insertions"],
    )


def _plus(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(a + b for a, b in zip(left, right))


def _prefix_vectors(ordered: Sequence[tuple[int, ...]]) -> list[tuple[int, ...]]:
    prefixes = [_ZERO]
    for vector in ordered:
        prefixes.append(_plus(prefixes[-1], vector))
    return prefixes


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _metrics(total: tuple[int, ...], selected: tuple[int, ...], count: int, target_us: int) -> dict[str, Any]:
    residual_errors = total[1] - selected[1]
    residual_units = total[2] - selected[2]
    return {
        "selected_count": count,
        "target_audio_s": target_us / 1_000_000,
        "selected_audio_s": selected[0] / 1_000_000,
        "unused_budget_s": (target_us - selected[0]) / 1_000_000,
        "actual_audio_fraction": _ratio(selected[0], total[0]),
        "captured_errors": selected[1],
        "captured_reference_units": selected[2],
        "selected_wer": _ratio(selected[1], selected[2]),
        "error_capture_rate": _ratio(selected[1], total[1]),
        "captured_error_clips": selected[3],
        "error_clip_recall": _ratio(selected[3], total[3]),
        "residual_errors": residual_errors,
        "residual_reference_units": residual_units,
        "residual_wer": _ratio(residual_errors, residual_units),
        "captured_asr_failure_errors": selected[4],
        "qc_unavailable_selected_count": selected[5],
    }


def selection_metrics(
    all_rows: Sequence[Mapping[str, Any]],
    selected_rows: Sequence[Mapping[str, Any]],
    target_us: int,
) -> dict[str, Any]:
    """Measure a selected subset against gold; no claim of actual correction.

    Error capture means errors *contained in selected recordings*, not errors
    a reviewer has identified or corrected. Residual WER excludes those clips.
    """
    _validate_gold(all_rows)
    _feature_rows(selected_rows)
    target_us = _count(target_us, "target_us")
    by_id = {row["item_id"]: row for row in all_rows}
    total = _ZERO
    for row in all_rows:
        total = _plus(total, _row_vector(row))
    if target_us > total[0]:
        raise ValueError("target_us exceeds total audio duration")
    selected = _ZERO
    for row in selected_rows:
        if row["item_id"] not in by_id:
            raise ValueError("selected item is absent from all_rows")
        # Gold comes exclusively from all_rows, never from a modified copy.
        selected = _plus(selected, _row_vector(by_id[row["item_id"]]))
    if selected[0] > target_us:
        raise ValueError("selected audio exceeds target_us")
    return _metrics(total, selected, len(selected_rows), target_us)


def _quantile(values: list[float], q: float) -> float:
    position = (len(values) - 1) * q
    left = math.floor(position)
    right = math.ceil(position)
    return values[left] + (values[right] - values[left]) * (position - left)


def _aggregate(trials: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"repetitions": len(trials)}
    for field in trials[0]:
        values = sorted(trial[field] for trial in trials if trial[field] is not None)
        result[field] = {
            "mean": fmean(values) if values else None,
            "median": median(values) if values else None,
            "p2_5": _quantile(values, 0.025) if values else None,
            "p97_5": _quantile(values, 0.975) if values else None,
            "defined_count": len(values),
        }
    return result


def _evaluation_config(config: Mapping[str, Any] | None) -> tuple[list[int], int, int]:
    settings = {**DEFAULT_CONFIG, **(config or {})}
    if set(settings) != set(DEFAULT_CONFIG):
        raise ValueError("unknown evaluation configuration key")
    if settings["tie_salt"] != TIE_SALT:
        raise ValueError("tie_salt is frozen by protocol")
    budgets = [_budget(value) for value in settings["budgets_percent"]]
    if not budgets or len(set(budgets)) != len(budgets):
        raise ValueError("budgets must be nonempty and unique")
    repetitions = _count(settings["random_repetitions"], "random_repetitions")
    if not repetitions:
        raise ValueError("random_repetitions must be positive")
    seed = _count(settings["random_seed"], "random_seed")
    return budgets, repetitions, seed


def _group_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    for row in rows:
        if not isinstance(row["source_split"], str) or not row["source_split"]:
            raise ValueError("source_split must be a nonempty string")
    grouped: dict[str, list[Mapping[str, Any]]] = {"pooled": list(rows)}
    for source_split in sorted({row["source_split"] for row in rows}):
        grouped[f"split:{source_split}"] = [row for row in rows if row["source_split"] == source_split]
    return grouped


def _duration_prefixes(durations: Sequence[int]) -> list[int]:
    prefixes = [0]
    for duration in durations:
        prefixes.append(prefixes[-1] + duration)
    return prefixes


def _planned_budgets(elapsed: list[int], budgets: list[int]) -> dict[str, dict[str, int]]:
    result = {}
    for budget in budgets:
        target_us = elapsed[-1] * budget // 100
        count = bisect_right(elapsed, target_us) - 1
        result[str(budget)] = {
            "selected_count": count,
            "target_audio_us": target_us,
            "selected_audio_us": elapsed[count],
        }
    return result


def build_selection_plan(
    rows: Sequence[Mapping[str, Any]], config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal every ranking and budget choice before any gold is read.

    Only IDs, source split, duration, ASR/QC status and issue features are read.
    Each group includes deterministic method ``order_ids`` and per-budget
    integer-microsecond selections. Its ``random`` list contains one entry per
    seed, with the same budget selections and the full-order SHA-256, without
    repeating thousands of IDs. The hash input is UTF-8 JSON of the ID list,
    using sort_keys=True, separators=(",", ":"), ensure_ascii=False and
    allow_nan=False. Seed, stable item-ID ordering and this hash allow replay.
    """
    _feature_rows(rows)
    budgets, repetitions, seed = _evaluation_config(config)
    output: dict[str, Any] = {"groups": {}}
    for group_name, group_rows in _group_rows(rows).items():
        stable_rows = sorted(group_rows, key=lambda row: row["item_id"])
        stable_ids = [row["item_id"] for row in stable_rows]
        durations = [_duration_us(row) for row in stable_rows]
        durations_by_id = dict(zip(stable_ids, durations))
        result = {
            "count": len(group_rows), "total_audio_us": sum(durations),
            "methods": {}, "random": [],
        }
        for method in METHODS:
            order_ids = [row["item_id"] for row in rank_rows(group_rows, method)]
            elapsed = _duration_prefixes([durations_by_id[item_id] for item_id in order_ids])
            result["methods"][method] = {
                "order_ids": order_ids, "budgets": _planned_budgets(elapsed, budgets),
            }
        for repetition in range(repetitions):
            trial_seed = seed + repetition
            order = list(range(len(stable_rows)))
            random.Random(trial_seed).shuffle(order)
            order_ids = [stable_ids[index] for index in order]
            encoded_order = json.dumps(
                order_ids, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")
            elapsed = _duration_prefixes([durations[index] for index in order])
            result["random"].append({
                "seed": trial_seed,
                "order_sha256": sha256(encoded_order).hexdigest(),
                "budgets": _planned_budgets(elapsed, budgets),
            })
        output["groups"][group_name] = result
    return output


def evaluate_rankings(rows: Sequence[Mapping[str, Any]], config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate pooled and per-split rankings, with reproducible random orders.

    Group keys are ``pooled`` and ``split:<source_split>``. Method budget keys
    are integer percentages as strings. Random trials flatten group, seed,
    budget_percent and metric fields for direct JSONL serialization. A seed
    uses one shuffled order for every budget within a group.
    """
    _validate_gold(rows)
    budgets, repetitions, seed = _evaluation_config(config)
    grouped = _group_rows(rows)
    output: dict[str, Any] = {"groups": {}, "random_trials": []}

    for group_name, group_rows in grouped.items():
        stable_rows = sorted(group_rows, key=lambda row: row["item_id"])
        vectors = [_row_vector(row) for row in stable_rows]
        vectors_by_id = {row["item_id"]: vector for row, vector in zip(stable_rows, vectors)}
        total = _prefix_vectors(vectors)[-1]
        targets = {budget: total[0] * budget // 100 for budget in budgets}
        result = {
            "totals": {
                "count": len(stable_rows), "speaker_count": len({row["speaker_id"] for row in stable_rows}),
                "audio_duration_us": total[0], "audio_duration_s": total[0] / 1_000_000,
                "errors": total[1], "reference_units": total[2], "error_clips": total[3],
                "asr_failure_errors": total[4], "qc_unavailable_count": total[5],
                "substitutions": total[6], "deletions": total[7], "insertions": total[8],
                "wer": _ratio(total[1], total[2]),
            },
            "methods": {}, "random": {},
        }
        for method in METHODS:
            ordered = rank_rows(group_rows, method)
            prefixes = _prefix_vectors([vectors_by_id[row["item_id"]] for row in ordered])
            elapsed = [vector[0] for vector in prefixes]
            result["methods"][method] = {}
            for budget in budgets:
                count = bisect_right(elapsed, targets[budget]) - 1
                result["methods"][method][str(budget)] = _metrics(total, prefixes[count], count, targets[budget])
        trials_by_budget: dict[int, list[dict[str, Any]]] = {budget: [] for budget in budgets}
        for repetition in range(repetitions):
            order = list(range(len(vectors)))
            trial_seed = seed + repetition
            random.Random(trial_seed).shuffle(order)
            prefixes = _prefix_vectors([vectors[index] for index in order])
            elapsed = [vector[0] for vector in prefixes]
            for budget in budgets:
                count = bisect_right(elapsed, targets[budget]) - 1
                metrics = _metrics(total, prefixes[count], count, targets[budget])
                trials_by_budget[budget].append(metrics)
                output["random_trials"].append({
                    "group": group_name, "seed": trial_seed, "budget_percent": budget, **metrics,
                })
        for budget in budgets:
            result["random"][str(budget)] = _aggregate(trials_by_budget[budget])
        output["groups"][group_name] = result
    return output
