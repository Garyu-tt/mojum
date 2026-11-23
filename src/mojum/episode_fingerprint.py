"""Bounded deterministic witness for public synthetic episode transitions."""
from __future__ import annotations
import hashlib
import json
import math
from collections.abc import Mapping, Sequence

SCHEMA = "mojum.episode-fingerprint/1"
MAX_TRANSITIONS = 10000
MAX_BYTES = 4_000_000

class FingerprintError(ValueError):
    """Invalid or oversized public synthetic episode."""

def _normalize(value, depth=0):
    if depth > 24:
        raise FingerprintError("nesting limit exceeded")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > 4096:
            raise FingerprintError("string too long")
        if isinstance(value, int) and value.bit_length() > 64:
            raise FingerprintError("integer too large")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FingerprintError("non-finite float")
        return value
    if isinstance(value, Mapping):
        if len(value) > 256 or any(not isinstance(k, str) for k in value):
            raise FingerprintError("invalid mapping keys")
        return {k: _normalize(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > 1024:
            raise FingerprintError("array too long")
        return [_normalize(v, depth + 1) for v in value]
    raise FingerprintError("unsupported value type")

def fingerprint_episode(transitions: Sequence[Mapping], *, episode_id: str) -> dict:
    """Produce a content witness, without echoing observation/action values."""
    if not isinstance(episode_id, str) or not 1 <= len(episode_id) <= 128:
        raise FingerprintError("invalid episode id")
    if not isinstance(transitions, (list, tuple)) or not 1 <= len(transitions) <= MAX_TRANSITIONS:
        raise FingerprintError("invalid transition count")
    h = hashlib.sha256()
    total = 0
    for index, transition in enumerate(transitions):
        if not isinstance(transition, Mapping) or set(transition) != {"tick", "observation", "action", "reward", "terminated", "truncated"}:
            raise FingerprintError("invalid transition shape")
        if type(transition["tick"]) is not int or transition["tick"] != index:
            raise FingerprintError("ticks must be contiguous starting at zero")
        if type(transition["terminated"]) is not bool or type(transition["truncated"]) is not bool:
            raise FingerprintError("terminal flags must be booleans")
        if type(transition["reward"]) not in (int, float) or not math.isfinite(transition["reward"]):
            raise FingerprintError("reward must be finite numeric")
        if index < len(transitions) - 1 and (transition["terminated"] or transition["truncated"]):
            raise FingerprintError("terminal transition must be last")
        canonical = json.dumps(_normalize(transition), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
        total += len(canonical) + 8
        if total > MAX_BYTES:
            raise FingerprintError("episode byte budget exceeded")
        h.update(len(canonical).to_bytes(8, "big"))
        h.update(canonical)
    domain = hashlib.sha256((SCHEMA + "\0" + episode_id).encode("utf-8")).digest()
    return {"schema": SCHEMA, "episode_id": episode_id, "transition_count": len(transitions),
            "sha256": hashlib.sha256(domain + h.digest()).hexdigest()}
