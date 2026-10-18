"""Allowlisted aggregate features; this module never reads reference labels.

No IDs, speakers, split labels, paths, vocabulary identities, reference text or
error counts enter a feature vector. The only text inspected is ASR output.
Missing confidence remains None; its availability is explicit, never fabricated.
Language-identification probability is audited separately and is not a model
feature or a transcription-confidence estimate.
"""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any

FEATURE_VERSION = "speechrank-features-v1"
ISSUE_CODES = (
    "low_level", "low_energy", "near_clipping", "dc_offset",
    "speech_without_transcript", "transcript_without_speech",
)
_TIMING_NAMES = (
    "count_per_s", "invalid_fraction", "zero_fraction", "outside_fraction",
    "out_of_order_fraction", "duration_mean_s", "duration_std_s",
    "union_fraction", "overlap_fraction", "internal_gap_mean_fraction",
    "internal_gap_max_fraction", "leading_gap_fraction", "trailing_gap_fraction",
)
_CONFIDENCE_SOURCES = {
    "word_probability": ("words", "probability", 0.0, 1.0),
    "segment_avg_logprob": ("segments", "avg_logprob", None, 0.0),
    "segment_no_speech_prob": ("segments", "no_speech_prob", 0.0, 1.0),
    "segment_compression_ratio": ("segments", "compression_ratio", 0.0, None),
    "segment_temperature": ("segments", "temperature", 0.0, None),
}
_CONFIDENCE_NAMES = tuple(
    f"{source}_{metric}" for source in _CONFIDENCE_SOURCES
    for metric in ("mean", "min", "max", "std", "available_fraction", "missing")
) + ("word_probability_below_half_fraction",)
_LEGACY_NAMES = (
    "log_duration_s", "asr_failed", "text_empty", "text_chars_per_s", "text_tokens_per_s",
    "text_mean_token_chars", "text_unique_token_fraction", "text_adjacent_repeat_fraction",
    "text_longest_token_run_fraction", "text_digit_fraction", "text_punctuation_fraction",
) + tuple(f"{kind}_{metric}" for kind in ("word", "segment") for metric in _TIMING_NAMES) + (
    "qc_missing", "qc_incomplete", "qc_vad_unavailable", "qc_coverage_unavailable",
    "qc_peak_dbfs", "qc_rms_dbfs", "qc_near_full_scale_ratio", "qc_low_energy_ratio", "qc_dc_offset",
    "qc_vad_speech_fraction", "qc_vad_intervals_per_s", "qc_coverage_basis_words",
    "qc_coverage_speech_fraction", "qc_coverage_covered_fraction", "qc_coverage_uncovered_fraction",
    "qc_coverage_uncovered_ratio", "qc_coverage_text_outside_fraction", "qc_priority_score",
) + tuple(f"qc_{code}_{metric}" for code in ISSUE_CODES for metric in ("count_per_s", "union_fraction"))
FEATURE_NAMES = {"legacy": _LEGACY_NAMES, "confidence": _LEGACY_NAMES + _CONFIDENCE_NAMES}


def _finite(value: Any, low=None, high=None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(value) or (low is not None and value < low) or (high is not None and value > high):
        return None
    return value


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("timestamp rows must be a list or tuple")  # noqa: TRY004 - uniform malformed-data error
    if any(not isinstance(row, Mapping) for row in value):
        raise ValueError("timestamp rows must contain mappings")
    return list(value)


def _union(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(end, merged[-1][1])
        else:
            merged.append((start, end))
    return merged


def _intervals(rows: Sequence[Mapping[str, Any]], duration: float) -> list[tuple[float, float]]:
    result = []
    for row in rows:
        start, end = _finite(row.get("start"), 0), _finite(row.get("end"), 0)
        if start is not None and end is not None and end > start and start < duration:
            result.append((start, min(end, duration)))
    return result


def _timing(rows: Sequence[Mapping[str, Any]], duration: float) -> dict[str, float | None]:
    count = len(rows)
    valid = _intervals(rows, duration)
    invalid = zero = outside = reordered = 0
    previous = None
    for row in rows:
        start, end = _finite(row.get("start")), _finite(row.get("end"))
        if start is None or end is None or start < 0 or end < start:
            invalid += 1
            continue
        zero += end == start
        outside += end > duration + 1e-6 or start > duration + 1e-6
        if previous is not None and start < previous:
            reordered += 1
        previous = start
    widths = [end - start for start, end in valid]
    merged = _union(valid)
    union_s = sum(end - start for start, end in merged)
    gaps = [merged[index][0] - merged[index - 1][1] for index in range(1, len(merged))]
    return {
        "count_per_s": count / duration,
        "invalid_fraction": invalid / count if count else 0.0,
        "zero_fraction": zero / count if count else 0.0,
        "outside_fraction": outside / count if count else 0.0,
        "out_of_order_fraction": reordered / max(count - 1, 1),
        "duration_mean_s": statistics.fmean(widths) if widths else None,
        "duration_std_s": statistics.pstdev(widths) if widths else None,
        "union_fraction": union_s / duration,
        "overlap_fraction": max(0.0, (sum(widths) - union_s) / sum(widths)) if widths else None,
        "internal_gap_mean_fraction": statistics.fmean(gaps) / duration if gaps else 0.0,
        "internal_gap_max_fraction": max(gaps, default=0.0) / duration,
        "leading_gap_fraction": merged[0][0] / duration if merged else 1.0,
        "trailing_gap_fraction": (duration - merged[-1][1]) / duration if merged else 1.0,
    }


def _priority_score(issues: Sequence[Mapping[str, Any]], duration: float) -> float:
    events: dict[float, list[tuple[int, int]]] = {}
    for row in issues:
        weight = {"low": 1, "medium": 2, "high": 3}.get(row.get("priority"))
        intervals = _intervals([row], duration)
        if weight is None or not intervals:
            continue
        start, end = intervals[0]
        events.setdefault(start, []).append((weight, 1))
        events.setdefault(end, []).append((weight, -1))
    active = [0, 0, 0, 0]
    integral = previous = 0.0
    for position in sorted(events):
        weight = next((value for value in (3, 2, 1) if active[value]), 0)
        integral += (position - previous) * weight
        for value, delta in events[position]:
            active[value] += delta
        previous = position
    return min(3.0, max(0.0, integral / duration))


def _confidence_values(rows: Sequence[Mapping[str, Any]], key: str, low, high) -> list[float]:
    return [value for row in rows if (value := _finite(row.get(key), low, high)) is not None]


def extract_features(asr_row: Mapping[str, Any], qc_report: Mapping[str, Any] | None = None,
                     arm: str = "legacy") -> dict[str, float | None]:
    """Extract the fixed feature allowlist from ASR/QC output, without gold access."""
    if arm not in FEATURE_NAMES:
        raise ValueError(f"unknown feature arm: {arm}")
    duration = _finite(asr_row.get("audio_duration_s"), 0)
    if duration is None or duration <= 0:
        raise ValueError("audio_duration_s must be a positive finite number")
    text = asr_row.get("asr_text", "")
    if not isinstance(text, str):
        raise ValueError("asr_text must be a string")  # noqa: TRY004 - uniform malformed-data error
    tokens = re.findall(r"\w+(?:['’]\w+)*", text.casefold(), flags=re.UNICODE)
    token_count = len(tokens)
    adjacent = sum(left == right for left, right in pairwise(tokens))
    longest = run = 0
    previous = None
    for token in tokens:
        run = run + 1 if token == previous else 1
        longest = max(longest, run)
        previous = token
    result: dict[str, float | None] = {
        "log_duration_s": math.log1p(duration),
        "asr_failed": float(asr_row.get("status", asr_row.get("asr_status")) != "succeeded"),
        "text_empty": float(not text.strip()), "text_chars_per_s": len(text) / duration,
        "text_tokens_per_s": token_count / duration,
        "text_mean_token_chars": statistics.fmean(map(len, tokens)) if tokens else 0.0,
        "text_unique_token_fraction": len(set(tokens)) / token_count if tokens else 0.0,
        "text_adjacent_repeat_fraction": adjacent / max(token_count - 1, 1),
        "text_longest_token_run_fraction": longest / token_count if tokens else 0.0,
        "text_digit_fraction": sum(char.isdigit() for char in text) / max(len(text), 1),
        "text_punctuation_fraction": sum(unicodedata.category(char).startswith("P") for char in text) / max(len(text), 1),
    }
    rows = {kind: _rows(asr_row.get(kind, [])) for kind in ("words", "segments")}
    for singular, plural in (("word", "words"), ("segment", "segments")):
        result.update({f"{singular}_{key}": value for key, value in _timing(rows[plural], duration).items()})
    if qc_report is None:
        qc_report = asr_row.get("audio_analysis")
    if qc_report is not None and not isinstance(qc_report, Mapping):
        raise ValueError("qc_report must be a mapping")
    report = qc_report or {}
    metrics, vad, coverage = (report.get(key) or {} for key in ("metrics", "vad", "coverage"))
    missing = not qc_report
    result.update({
        "qc_missing": float(missing), "qc_incomplete": float(report.get("status") != "complete"),
        "qc_vad_unavailable": float(vad.get("status") != "ok"),
        "qc_coverage_unavailable": float(coverage.get("status") != "ok"),
    })
    for key in ("peak_dbfs", "rms_dbfs", "near_full_scale_ratio", "low_energy_ratio", "dc_offset"):
        result[f"qc_{key}"] = _finite(metrics.get(key))
    vad_intervals = _intervals(_rows(vad.get("intervals", [])), duration)
    result["qc_vad_speech_fraction"] = sum(end - start for start, end in _union(vad_intervals)) / duration if vad.get("status") == "ok" else None
    result["qc_vad_intervals_per_s"] = len(vad_intervals) / duration if vad.get("status") == "ok" else None
    result["qc_coverage_basis_words"] = float(coverage.get("basis") == "words") if coverage.get("status") == "ok" else None
    for key in ("speech", "covered", "uncovered", "text_outside"):
        value = _finite(coverage.get(f"{key}_s"), 0)
        result[f"qc_coverage_{key}_fraction"] = value / duration if value is not None else None
    result["qc_coverage_uncovered_ratio"] = _finite(coverage.get("uncovered_ratio"), 0, 1)
    issues = _rows(report.get("issues", []))
    result["qc_priority_score"] = _priority_score(issues, duration) if not missing else None
    for code in ISSUE_CODES:
        selected = [issue for issue in issues if issue.get("code") == code]
        result[f"qc_{code}_count_per_s"] = len(selected) / duration if not missing else None
        result[f"qc_{code}_union_fraction"] = sum(end - start for start, end in _union(_intervals(selected, duration))) / duration if not missing else None
    if arm == "confidence":
        for source, (kind, key, low, high) in _CONFIDENCE_SOURCES.items():
            values = _confidence_values(rows[kind], key, low, high)
            result.update({
                f"{source}_mean": statistics.fmean(values) if values else None,
                f"{source}_min": min(values) if values else None,
                f"{source}_max": max(values) if values else None,
                f"{source}_std": statistics.pstdev(values) if values else None,
                f"{source}_available_fraction": len(values) / len(rows[kind]) if rows[kind] else 0.0,
                f"{source}_missing": float(not values),
            })
        probabilities = _confidence_values(rows["words"], "probability", 0, 1)
        result["word_probability_below_half_fraction"] = sum(value < 0.5 for value in probabilities) / len(probabilities) if probabilities else None
    if set(result) != set(FEATURE_NAMES[arm]):
        raise AssertionError("feature implementation/schema mismatch")
    return {name: result[name] for name in FEATURE_NAMES[arm]}


def audit_confidence_availability(asr_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Report persisted confidence availability without reading labels or IDs."""
    sources = {**_CONFIDENCE_SOURCES, "language_probability": (None, "language_probability", 0.0, 1.0)}
    result: dict[str, Any] = {"rows": len(asr_rows), "feature_version": FEATURE_VERSION, "sources": {}}
    for source, (kind, key, low, high) in sources.items():
        values = []
        records = present = invalid = rows_with_valid = 0
        for row in asr_rows:
            entries = [row] if kind is None else _rows(row.get(kind, []))
            found = False
            for entry in entries:
                records += 1
                raw = entry.get(key)
                if raw is not None:
                    present += 1
                    value = _finite(raw, low, high)
                    if value is None:
                        invalid += 1
                    else:
                        values.append(value)
                        found = True
            rows_with_valid += found
        result["sources"][source] = {
            "records": records, "present": present, "valid": len(values),
            "missing": records - present, "invalid": invalid, "rows_with_valid": rows_with_valid,
            "constant": len(set(values)) == 1 if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None,
            "used_as_model_feature": source != "language_probability",
        }
    result["language_probability_note"] = "Language-identification probability; forced-language output may be constant and is not transcription confidence. Excluded from both feature arms."
    return result
