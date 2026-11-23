"""Bounded interval statistics for PUBLIC_SYNTHETIC pulse-frame fixtures.

This module reports observations in a caller-supplied logical clock. It does
not infer physiology, fill missing samples, or interact with an organism.
"""

from __future__ import annotations

import hashlib
import json

SCHEMA = "mojum.synthetic-pulse-interval-report/1"
CLASSIFICATION = "PUBLIC_SYNTHETIC"
MAX_FRAMES = 10_000
MAX_TICK = 2**63 - 1
MIN_DT_NS = 1_000
MAX_DT_NS = 1_000_000_000


class PulseIntervalError(ValueError):
    """Invalid or out-of-budget synthetic pulse input."""


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise PulseIntervalError("Report is not canonical JSON") from exc


def _median_floor(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def analyze_pulse_intervals(
    frames: list[dict[str, object]] | tuple[dict[str, object], ...],
    *,
    dt_ns: int,
    classification: str,
) -> dict[str, object]:
    """Analyze rising edges in exact {"tick": int, "pulse": bool} frames.

    Ticks use a caller-declared fixed logical step dt_ns. A missing tick
    starts a new continuity segment: an interval is never calculated across
    that gap, and an initial high sample in any segment is not assumed to be
    a rising edge. The first edge in a segment is therefore observed only
    after an explicit False then True transition.

    The report is a deterministic diagnostic over synthetic data, not a
    heart-rate/medical measurement or prediction.
    """
    if type(classification) is not str or classification != CLASSIFICATION:
        raise PulseIntervalError("Only PUBLIC_SYNTHETIC input is accepted")
    if type(dt_ns) is not int or not MIN_DT_NS <= dt_ns <= MAX_DT_NS:
        raise PulseIntervalError("dt_ns must be an integer in [1000, 1000000000]")
    if type(frames) not in (list, tuple) or not 1 <= len(frames) <= MAX_FRAMES:
        raise PulseIntervalError(f"frames must contain 1..{MAX_FRAMES} entries")

    max_tick = MAX_TICK // dt_ns
    previous_tick: int | None = None
    previous_pulse: bool | None = None
    segment = 0
    missing_frames = 0
    rising_edges: list[tuple[int, int]] = []
    unanchored_high_segments = 0

    for index, frame in enumerate(frames):
        if type(frame) is not dict or set(frame) != {"tick", "pulse"}:
            raise PulseIntervalError(f"frames[{index}] requires exactly tick and pulse")
        tick, pulse = frame["tick"], frame["pulse"]
        if type(tick) is not int or not 0 <= tick <= max_tick:
            raise PulseIntervalError(f"frames[{index}].tick is outside the supported range")
        if type(pulse) is not bool:
            raise PulseIntervalError(f"frames[{index}].pulse must be an exact bool")
        if previous_tick is not None:
            if tick <= previous_tick:
                raise PulseIntervalError("ticks must be strictly increasing")
            if tick != previous_tick + 1:
                missing_frames += tick - previous_tick - 1
                segment += 1
                previous_pulse = None
        if previous_pulse is None and pulse:
            unanchored_high_segments += 1
        elif previous_pulse is False and pulse:
            rising_edges.append((segment, tick * dt_ns))
        previous_tick = tick
        previous_pulse = pulse

    intervals: list[int] = []
    previous_edge: tuple[int, int] | None = None
    for edge in rising_edges:
        if previous_edge is not None and edge[0] == previous_edge[0]:
            intervals.append(edge[1] - previous_edge[1])
        previous_edge = edge

    report: dict[str, object] = {
        "schema": SCHEMA,
        "classification": CLASSIFICATION,
        "dt_ns": dt_ns,
        "frame_count": len(frames),
        "rising_edge_count": len(rising_edges),
        "interval_count": len(intervals),
        "missing_frame_count": missing_frames,
        "unanchored_high_segment_count": unanchored_high_segments,
        "intervals_excluded_at_gaps": max(0, len(rising_edges) - len(intervals) - 1),
        "intervals_ns": intervals,
        "minimum_interval_ns": min(intervals) if intervals else None,
        "maximum_interval_ns": max(intervals) if intervals else None,
        "mean_interval_ns_floor": sum(intervals) // len(intervals) if intervals else None,
        "median_interval_ns_floor": _median_floor(intervals),
        "status": "OK" if intervals else "INSUFFICIENT_CONTIGUOUS_PULSES",
        "provenance": "caller-supplied-logical-clock-synthetic-only/v1",
    }
    report["sha256"] = hashlib.sha256(_canonical(report).encode("utf-8")).hexdigest()
    return report
