"""Bounded offline liveness audit for PUBLIC_SYNTHETIC body observations.

This is a timing/sequence consistency check over caller-supplied data. It is
not a sensor driver, physical liveness detector, authentication mechanism, or
proof that a simulated/physical organism is healthy.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass

OBSERVATION_SCHEMA = "mojum.body.observation/1"
PUBLIC_SYNTHETIC = "PUBLIC_SYNTHETIC"
MAX_FRAMES = 4096
MAX_FRAME_BYTES = 524_288
MAX_ID_CHARS = 96
MAX_COUNTER = 2**63 - 1
_REQUIRED_KEYS = frozenset({
    "schema", "visibility", "consent", "stream_id", "source_id",
    "sequence", "sample_time_ns",
})


class ObservationLivenessError(ValueError):
    """Invalid or unsupported observation stream."""


@dataclass(frozen=True, slots=True)
class LivenessReport:
    """Detached, immutable summary; caller frame payloads are never retained."""

    stream_id: str
    source_id: str
    frame_count: int
    first_sequence: int
    last_sequence: int
    missing_sequence_count: int
    sequence_gap_ranges: tuple[tuple[int, int], ...]
    max_inter_sample_gap_ns: int
    max_sample_gap_ns: int
    age_ns: int | None
    max_age_ns: int
    is_fresh: bool
    within_sample_gap_budget: bool
    is_contiguous: bool
    is_live: bool
    reasons: tuple[str, ...]
    sha256: str


def _fail(message: str) -> None:
    raise ObservationLivenessError(message)


def _native_int(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_COUNTER:
        _fail(f"{name} must be a native integer in [{minimum}, {MAX_COUNTER}]")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or len(value) > MAX_ID_CHARS:
        _fail(f"{name} must be nonempty text of at most {MAX_ID_CHARS} characters")
    if value.strip() != value:
        _fail(f"{name} must not have surrounding whitespace")
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Cs") for ch in value):
        _fail(f"{name} contains control, format, or surrogate characters")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ObservationLivenessError(f"{name} must be valid UTF-8") from exc
    return value


def _admit_frame(frame: object, index: int) -> dict[str, object]:
    if type(frame) is not dict:
        _fail(f"frames[{index}] must be a native dict")
    if any(type(key) is not str for key in frame):
        _fail(f"frames[{index}] contains a non-string key")
    if set(frame) != _REQUIRED_KEYS:
        _fail(f"frames[{index}] must contain exactly {sorted(_REQUIRED_KEYS)}")

    if type(frame["schema"]) is not str or frame["schema"] != OBSERVATION_SCHEMA:
        _fail(f"frames[{index}] has an unsupported schema")
    if type(frame["visibility"]) is not str or frame["visibility"] != PUBLIC_SYNTHETIC:
        _fail(f"frames[{index}] must be explicitly PUBLIC_SYNTHETIC")
    if type(frame["consent"]) is not str or frame["consent"] != "explicit":
        _fail(f"frames[{index}] requires explicit consent")

    return {
        "schema": OBSERVATION_SCHEMA,
        "visibility": PUBLIC_SYNTHETIC,
        "consent": "explicit",
        "stream_id": _text(frame["stream_id"], f"frames[{index}].stream_id"),
        "source_id": _text(frame["source_id"], f"frames[{index}].source_id"),
        "sequence": _native_int(frame["sequence"], f"frames[{index}].sequence"),
        "sample_time_ns": _native_int(
            frame["sample_time_ns"], f"frames[{index}].sample_time_ns"
        ),
    }


def audit_observation_liveness(
    frames: object,
    *,
    evaluated_at_ns: int,
    max_age_ns: int,
    max_sample_gap_ns: int,
) -> LivenessReport:
    """Audit one bounded stream against caller-supplied monotonic-time limits.

    Sequence gaps are reported rather than expanded into an unbounded list.
    A stream is live only when it is contiguous, each sample interval is within
    budget, and its newest sample is neither stale nor in the future.
    """
    evaluated_at_ns = _native_int(evaluated_at_ns, "evaluated_at_ns")
    max_age_ns = _native_int(max_age_ns, "max_age_ns", minimum=1)
    max_sample_gap_ns = _native_int(max_sample_gap_ns, "max_sample_gap_ns", minimum=1)
    if type(frames) is not list or not 1 <= len(frames) <= MAX_FRAMES:
        _fail(f"frames must be a native list containing 1..{MAX_FRAMES} records")

    admitted = [_admit_frame(frame, i) for i, frame in enumerate(frames)]
    first = admitted[0]
    stream_id, source_id = first["stream_id"], first["source_id"]
    previous_sequence = None
    previous_time = None
    gap_ranges: list[tuple[int, int]] = []
    missing_count = 0
    max_interval = 0

    for index, frame in enumerate(admitted):
        if frame["stream_id"] != stream_id or frame["source_id"] != source_id:
            _fail(f"frames[{index}] changes stream or source identity")
        sequence = frame["sequence"]
        sample_time = frame["sample_time_ns"]
        if previous_sequence is not None:
            if sequence <= previous_sequence:
                _fail(f"frames[{index}] sequence must strictly increase")
            if sample_time <= previous_time:
                _fail(f"frames[{index}] sample_time_ns must strictly increase")
            if sequence - previous_sequence > 1:
                gap_ranges.append((previous_sequence + 1, sequence - 1))
                missing_count += sequence - previous_sequence - 1
            max_interval = max(max_interval, sample_time - previous_time)
        previous_sequence, previous_time = sequence, sample_time

    try:
        canonical = json.dumps(
            admitted, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise ObservationLivenessError("observation stream is not canonical JSON") from exc
    if len(canonical) > MAX_FRAME_BYTES:
        _fail(f"canonical observation stream exceeds {MAX_FRAME_BYTES} bytes")

    last_time = admitted[-1]["sample_time_ns"]
    age = evaluated_at_ns - last_time
    reasons: list[str] = []
    contiguous = missing_count == 0
    within_gap = max_interval <= max_sample_gap_ns
    fresh = 0 <= age <= max_age_ns
    if not contiguous:
        reasons.append("sequence_gap")
    if not within_gap:
        reasons.append("sample_gap_exceeded")
    if age < 0:
        reasons.append("future_sample")
    elif age > max_age_ns:
        reasons.append("stale")
    live = contiguous and within_gap and fresh
    return LivenessReport(
        stream_id=stream_id,
        source_id=source_id,
        frame_count=len(admitted),
        first_sequence=admitted[0]["sequence"],
        last_sequence=admitted[-1]["sequence"],
        missing_sequence_count=missing_count,
        sequence_gap_ranges=tuple(gap_ranges),
        max_inter_sample_gap_ns=max_interval,
        max_sample_gap_ns=max_sample_gap_ns,
        age_ns=age,
        max_age_ns=max_age_ns,
        is_fresh=fresh,
        within_sample_gap_budget=within_gap,
        is_contiguous=contiguous,
        is_live=live,
        reasons=tuple(reasons),
        sha256=hashlib.sha256(canonical).hexdigest(),
    )
