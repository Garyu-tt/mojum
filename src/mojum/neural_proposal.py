"""Bounded, proposal-only neural backend wire for future ANNarchy/Brian2.

This module deliberately has no neural engine import and never actuates a
body: any later spinal motor gate must separately evaluate its proposals.
"""
from __future__ import annotations

import json
import math
import re


_MAX_WIRE_I64 = 2**63 - 1
_SCHEMA = "mojum.neural-proposal/1"
_PORT = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def _finite(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


def validate_neural_proposal(packet: object) -> dict:
    """Validate and return detached JSON-safe sensory/motor proposal only."""
    if type(packet) is not dict or set(packet) != {
        "schema", "tick", "observed_at_ns", "backend", "mode",
        "learning_revision", "sensory", "motor_proposal",
    }:
        raise ValueError("invalid neural packet fields")
    if type(packet["schema"]) is not str or packet["schema"] != _SCHEMA:
        raise ValueError("unsupported neural wire")
    if type(packet["tick"]) is not int or not 0 <= packet["tick"] <= _MAX_WIRE_I64:
        raise ValueError("invalid tick")
    if type(packet["observed_at_ns"]) is not int or not 0 <= packet["observed_at_ns"] <= _MAX_WIRE_I64:
        raise ValueError("invalid observation time")
    if type(packet["backend"]) is not str or packet["backend"] not in (
        "fixture", "annarchy", "brian2",
    ):
        raise ValueError("unknown experimental neural backend")
    if type(packet["mode"]) is not str or packet["mode"] not in ("shadow", "proposal"):
        raise ValueError("invalid neural mode")
    if type(packet["learning_revision"]) is not str or _SHA.fullmatch(packet["learning_revision"]) is None:
        raise ValueError("invalid neural state revision")
    samples = packet["sensory"]
    if type(samples) is not list or len(samples) > 64:
        raise ValueError("invalid sensory samples")
    seen = set()
    for sample in samples:
        if type(sample) is not dict or set(sample) != {"port", "value"}:
            raise ValueError("invalid sensory sample")
        name = sample["port"]
        if type(name) is not str or _PORT.fullmatch(name) is None or name in seen:
            raise ValueError("invalid or duplicate sensory port")
        seen.add(name)
        if not _finite(sample["value"]) or abs(sample["value"]) > 1_000_000:
            raise ValueError("invalid sensory value")
    proposed = packet["motor_proposal"]
    if packet["mode"] == "shadow":
        if proposed is not None:
            raise ValueError("shadow-only model may not submit motor proposal")
    elif proposed is not None:
        if type(proposed) is not dict or set(proposed) != {"dx", "dy"}:
            raise ValueError("invalid motor proposal")
        dx, dy = proposed["dx"], proposed["dy"]
        if not _finite(dx) or not _finite(dy) or math.hypot(dx, dy) > 0.25:
            raise ValueError("unbounded motor proposal")
    # Even 'proposal' does not authorize applying it to a simulated or real
    # body. A separate time/fencing/permission-aware motor gate is mandatory.
    try:
        return json.loads(json.dumps(
            packet, sort_keys=True, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid neural JSON") from exc
