"""Observable QC review priority shared by the runtime and evaluation tools.

This module uses only the standard library and never reads gold labels.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

TIE_SALT = "audio-qc-ranking-v1:"
PRIORITIES = {"low": 1, "medium": 2, "high": 3}


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


def priority_score(
    issues: Sequence[Mapping[str, Any]],
    duration: float,
    allowed_codes: frozenset[str] | set[str] | None = None,
) -> float:
    """Integrate maximum active issue priority over time, divided by duration.

    Priorities are low=1, medium=2, high=3. Overlapping findings contribute only
    their maximum priority, so duplicate findings cannot inflate the score.
    Empty issues have score zero; zero-length intervals contribute zero.
    Endpoints may exceed duration by at most one microsecond solely to account
    for six-decimal report serialization; they are clamped to duration. This
    representation tolerance does not expand any acoustic matching interval.
    """
    duration = _number(duration, "duration")
    if duration <= 0:
        raise ValueError("duration must be positive")
    events: dict[float, list[tuple[int, int]]] = {}
    for issue in issues:
        start = _number(issue["start"], "issue.start")
        end = _number(issue["end"], "issue.end")
        priority = issue["priority"]
        if priority not in PRIORITIES:
            raise ValueError(f"unknown issue priority: {priority!r}")
        if not 0 <= start <= end <= duration + 1e-6:
            raise ValueError("issue interval must lie within the audio duration")
        start = min(start, duration)
        end = min(end, duration)
        if start == end or (allowed_codes is not None and issue.get("code") not in allowed_codes):
            continue
        weight = PRIORITIES[priority]
        events.setdefault(start, []).append((weight, 1))
        events.setdefault(end, []).append((weight, -1))
    active = [0, 0, 0, 0]
    previous = 0.0
    integral = 0.0
    for position in sorted(events):
        weight = next((p for p in (3, 2, 1) if active[p]), 0)
        integral += (position - previous) * weight
        for priority, change in events[position]:
            active[priority] += change
        previous = position
    return min(3.0, max(0.0, integral / duration))
