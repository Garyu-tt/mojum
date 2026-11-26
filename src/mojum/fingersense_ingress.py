"""Bounded synthetic FingerSense frame admission; no device or actuation access.

This module accepts caller-supplied PUBLIC_SYNTHETIC fingertip samples. It is
an input contract, not a sensor driver or a claim about physical contact.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


FRAME_SCHEMA = "mojum.fingersense-frame/1"
STATE_SCHEMA = "mojum.fingersense-ingress-state/1"
PUBLIC_SYNTHETIC = "PUBLIC_SYNTHETIC"
MAX_SOURCE_ID_CHARS = 96
MAX_FINGER_ID_CHARS = 32
MAX_CHANNEL_ID_CHARS = 32
MAX_SAMPLES = 64
MAX_TICK = 2**63 - 1
MAX_REVISION = 2**31 - 1
MAX_SEQUENCE = 2**63 - 1
MAX_VALUE_MILLI = 1_000_000
MAX_CANONICAL_BYTES = 32_768

_FRAME_KEYS = frozenset({
    "schema", "visibility", "consent", "source_id", "source_revision",
    "sequence", "tick", "samples",
})
_SAMPLE_KEYS = frozenset({"finger_id", "channel_id", "value_milli"})


class FingerSenseIngressError(ValueError):
    """Invalid, stale, or unsupported synthetic FingerSense input."""


@dataclass(frozen=True, slots=True)
class FingerSample:
    finger_id: str
    channel_id: str
    value_milli: int


@dataclass(frozen=True, slots=True)
class FingerSenseFrame:
    source_id: str
    source_revision: int
    sequence: int
    tick: int
    samples: tuple[FingerSample, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class IngressState:
    """Immutable high-water mark for one explicitly identified source."""

    source_id: str
    source_revision: int
    last_sequence: int
    last_tick: int
    frame_sha256: str


def _fail(message: str) -> None:
    raise FingerSenseIngressError(message)


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(f"{name} must be a native integer in [{minimum}, {maximum}]")
    return value


def _identifier(value: object, name: str, maximum: int) -> str:
    if type(value) is not str or not 1 <= len(value) <= maximum:
        _fail(f"{name} must be nonempty ASCII text of at most {maximum} characters")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
    if value[0] not in allowed[:62] or any(char not in allowed for char in value):
        _fail(f"{name} contains unsupported identifier characters")
    return value


def _exact_dict(value: object, keys: frozenset[str], name: str) -> dict:
    if type(value) is not dict or any(type(key) is not str for key in value):
        _fail(f"{name} must be a native dict with string keys")
    if set(value) != keys:
        _fail(f"{name} must contain exactly {sorted(keys)}")
    return value


def _canonical(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii", errors="strict")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise FingerSenseIngressError("frame is not canonical JSON") from exc
    if len(encoded) > MAX_CANONICAL_BYTES:
        _fail("canonical frame exceeds the byte budget")
    return encoded


def admit_frame(
    raw: object,
    previous: IngressState | None = None,
) -> tuple[FingerSenseFrame, IngressState]:
    """Admit one detached frame and advance its source's monotonic cursor.

    Sequence must increase by exactly one and ticks must strictly increase.
    A source revision change requires a fresh stream (previous=None); callers
    must explicitly establish a new epoch rather than silently reset history.
    """
    value = _exact_dict(raw, _FRAME_KEYS, "frame")
    if value["schema"] != FRAME_SCHEMA or type(value["schema"]) is not str:
        _fail("unsupported frame schema")
    if value["visibility"] != PUBLIC_SYNTHETIC or type(value["visibility"]) is not str:
        _fail("frame must be PUBLIC_SYNTHETIC")
    if value["consent"] != "explicit" or type(value["consent"]) is not str:
        _fail("frame requires explicit consent")

    source_id = _identifier(value["source_id"], "source_id", MAX_SOURCE_ID_CHARS)
    source_revision = _integer(value["source_revision"], "source_revision", 0, MAX_REVISION)
    sequence = _integer(value["sequence"], "sequence", 0, MAX_SEQUENCE)
    tick = _integer(value["tick"], "tick", 0, MAX_TICK)
    samples_raw = value["samples"]
    if type(samples_raw) is not list or not 1 <= len(samples_raw) <= MAX_SAMPLES:
        _fail(f"samples must be a native list containing 1..{MAX_SAMPLES} entries")

    samples: list[FingerSample] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(samples_raw):
        sample = _exact_dict(item, _SAMPLE_KEYS, f"samples[{index}]")
        finger_id = _identifier(sample["finger_id"], f"samples[{index}].finger_id", MAX_FINGER_ID_CHARS)
        channel_id = _identifier(sample["channel_id"], f"samples[{index}].channel_id", MAX_CHANNEL_ID_CHARS)
        value_milli = _integer(sample["value_milli"], f"samples[{index}].value_milli", 0, MAX_VALUE_MILLI)
        key = (finger_id, channel_id)
        if key in seen:
            _fail("frame contains a duplicate finger/channel sample")
        seen.add(key)
        samples.append(FingerSample(finger_id, channel_id, value_milli))
    samples.sort(key=lambda sample: (sample.finger_id, sample.channel_id))

    if previous is not None:
        if type(previous) is not IngressState:
            _fail("previous must be an IngressState")
        if source_id != previous.source_id or source_revision != previous.source_revision:
            _fail("source identity/revision changed; start an explicit new stream")
        if sequence != previous.last_sequence + 1:
            _fail("sequence must be contiguous with the previous frame")
        if tick <= previous.last_tick:
            _fail("tick must strictly increase")

    canonical = {
        "schema": FRAME_SCHEMA,
        "visibility": PUBLIC_SYNTHETIC,
        "consent": "explicit",
        "source_id": source_id,
        "source_revision": source_revision,
        "sequence": sequence,
        "tick": tick,
        "samples": [
            {"finger_id": sample.finger_id, "channel_id": sample.channel_id,
             "value_milli": sample.value_milli}
            for sample in samples
        ],
    }
    digest = hashlib.sha256(_canonical(canonical)).hexdigest()
    frame = FingerSenseFrame(
        source_id=source_id, source_revision=source_revision, sequence=sequence,
        tick=tick, samples=tuple(samples), sha256=digest,
    )
    state = IngressState(
        source_id=source_id, source_revision=source_revision,
        last_sequence=sequence, last_tick=tick, frame_sha256=digest,
    )
    return frame, state
