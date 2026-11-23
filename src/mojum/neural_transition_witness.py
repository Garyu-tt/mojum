"""Bounded read-only witness for one delayed-pulse predictor transition.

This module replays one PUBLIC_SYNTHETIC frame from a detached PulsePredictor
snapshot and compares the resulting state with a caller-supplied post-state.
It is an offline consistency check, not a predictor-quality or provenance claim.
"""

import copy
import hashlib
import json
import math

from mojum.neural import NeuralError, PulsePredictor, SCHEMA as PREDICTOR_SCHEMA

SCHEMA = "mojum.neural.transition-witness/1"
CLASSIFICATION = "PUBLIC_SYNTHETIC"
MAX_BYTES = 256 * 1024
MAX_NODES = 2048
MAX_DEPTH = 12
MAX_STRING_CHARS = 4096
_MAX_INT_BITS = 64
_SNAPSHOT_KEYS = {
    "schema", "learning_rate", "weights", "pending_features",
    "updates", "loss_sum", "last_observed_at_ns",
}
_FRAME_KEYS = {"kind", "observed_at_ns", "heartbeat"}
_HEARTBEAT_KEYS = {"phase_milli", "pulse", "synthetic"}


class TransitionWitnessError(ValueError):
    """Input is malformed, out of budget, or cannot be safely attested."""


def _fail(message):
    raise TransitionWitnessError(message)


def _canonical_bytes(value):
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise TransitionWitnessError("value is not canonical bounded JSON") from exc


def _digest(value):
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _admit_native_tree(value, *, max_bytes, max_nodes):
    """Reject subclasses, aliases, cycles and expensive trees before JSON work."""
    if type(max_bytes) is not int or not 128 <= max_bytes <= 1024 * 1024:
        _fail("invalid byte budget")
    if type(max_nodes) is not int or not 16 <= max_nodes <= MAX_NODES:
        _fail("invalid node budget")

    stack = [(value, 0)]
    seen = set()
    nodes = 0
    estimated_bytes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            _fail("node budget exceeded")
        if depth > MAX_DEPTH:
            _fail("nesting depth exceeded")

        kind = type(item)
        estimated_bytes += 1
        if kind is dict:
            marker = id(item)
            if marker in seen:
                _fail("container alias or cycle")
            seen.add(marker)
            if len(item) > max_nodes - nodes:
                _fail("node budget exceeded")
            for key, child in item.items():
                if type(key) is not str or len(key) > 128:
                    _fail("object keys must be bounded native strings")
                try:
                    key_size = len(key.encode("utf-8", "strict"))
                except UnicodeError as exc:
                    raise TransitionWitnessError("object key is not valid UTF-8") from exc
                estimated_bytes += key_size + 4
                if estimated_bytes > max_bytes:
                    _fail("encoded byte budget exceeded")
                stack.append((child, depth + 1))
        elif kind is list:
            marker = id(item)
            if marker in seen:
                _fail("container alias or cycle")
            seen.add(marker)
            if len(item) > max_nodes - nodes:
                _fail("node budget exceeded")
            for child in item:
                stack.append((child, depth + 1))
        elif kind is str:
            if len(item) > MAX_STRING_CHARS:
                _fail("string budget exceeded")
            try:
                estimated_bytes += len(item.encode("utf-8", "strict")) + 2
            except UnicodeError as exc:
                raise TransitionWitnessError("string is not valid UTF-8") from exc
        elif kind is int:
            if item.bit_length() > _MAX_INT_BITS:
                _fail("integer magnitude exceeded")
            estimated_bytes += len(str(item))
        elif kind is float:
            if not math.isfinite(item):
                _fail("non-finite number")
            estimated_bytes += 32
        elif kind not in (bool, type(None)):
            _fail("only exact native JSON values are accepted")
        if estimated_bytes > max_bytes:
            _fail("encoded byte budget exceeded")

    raw = _canonical_bytes(value)
    if len(raw) > max_bytes:
        _fail("encoded byte budget exceeded")
    return raw


def _validate_snapshot(snapshot, *, max_bytes, max_nodes):
    _admit_native_tree(snapshot, max_bytes=max_bytes, max_nodes=max_nodes)
    if type(snapshot) is not dict or set(snapshot) != _SNAPSHOT_KEYS:
        _fail("snapshot fields do not match predictor v1")
    if snapshot.get("schema") != PREDICTOR_SCHEMA:
        _fail("unsupported predictor snapshot schema")
    try:
        PulsePredictor.from_snapshot(snapshot, summation="ordered")
    except (NeuralError, TypeError, ValueError, OverflowError) as exc:
        raise TransitionWitnessError("invalid predictor snapshot") from exc


def _validate_frame(frame, *, max_bytes, max_nodes):
    _admit_native_tree(frame, max_bytes=max_bytes, max_nodes=max_nodes)
    if type(frame) is not dict or set(frame) != _FRAME_KEYS:
        _fail("sensory frame fields are not exact")
    if frame["kind"] != "interoception":
        _fail("interoception frame required")
    if type(frame["observed_at_ns"]) is not int or not 0 <= frame["observed_at_ns"] < 2**63:
        _fail("invalid logical timestamp")
    heartbeat = frame["heartbeat"]
    if type(heartbeat) is not dict or set(heartbeat) != _HEARTBEAT_KEYS:
        _fail("heartbeat fields are not exact")
    if (type(heartbeat["phase_milli"]) is not int
            or not 0 <= heartbeat["phase_milli"] < 1000
            or type(heartbeat["pulse"]) is not bool
            or heartbeat["synthetic"] is not True):
        _fail("invalid synthetic heartbeat values")


def attest_transition(
    before_snapshot,
    sensory_frame,
    after_snapshot,
    *,
    classification,
    summation,
    max_bytes=MAX_BYTES,
    max_nodes=MAX_NODES,
):
    """Replay one explicit synthetic frame and attest the caller's post-state.

    The summation algorithm is mandatory because predictor v1 snapshots do not
    persist it. The return value contains only digests, counters and mismatch
    field names, never raw snapshots or frames.
    """
    if type(classification) is not str or classification != CLASSIFICATION:
        _fail("explicit PUBLIC_SYNTHETIC classification required")
    if type(summation) is not str or summation not in ("ordered", "compensated"):
        _fail("explicit supported summation rule required")

    _validate_snapshot(before_snapshot, max_bytes=max_bytes, max_nodes=max_nodes)
    _validate_frame(sensory_frame, max_bytes=max_bytes, max_nodes=max_nodes)
    _validate_snapshot(after_snapshot, max_bytes=max_bytes, max_nodes=max_nodes)

    before = copy.deepcopy(before_snapshot)
    frame = copy.deepcopy(sensory_frame)
    supplied_after = copy.deepcopy(after_snapshot)
    try:
        predictor = PulsePredictor.from_snapshot(before, summation=summation)
        receipt = predictor.observe(frame)
    except (NeuralError, TypeError, ValueError, OverflowError) as exc:
        raise TransitionWitnessError("one-step replay was rejected") from exc

    expected_after = predictor.snapshot()
    mismatch_fields = sorted(
        key for key in _SNAPSHOT_KEYS
        if expected_after.get(key) != supplied_after.get(key)
    )
    before_digest = _digest(before)
    after_digest = _digest(supplied_after)
    expected_digest = _digest(expected_after)
    frame_digest = _digest(frame)
    receipt_digest = _digest(receipt)
    report = {
        "schema": SCHEMA,
        "status": "MATCH" if not mismatch_fields else "MISMATCH",
        "summation": summation,
        "updates_before": before["updates"],
        "updates_after": expected_after["updates"],
        "trained": receipt["trained"],
        "observed_at_ns": frame["observed_at_ns"],
        "mismatch_fields": mismatch_fields,
        "before_sha256": before_digest,
        "frame_sha256": frame_digest,
        "supplied_after_sha256": after_digest,
        "expected_after_sha256": expected_digest,
        "receipt_sha256": receipt_digest,
    }
    report["witness_sha256"] = _digest(report)
    return report
