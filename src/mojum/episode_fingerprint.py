"""Bounded deterministic witness for public synthetic episode transitions."""
from __future__ import annotations

import hashlib
import json
import math

SCHEMA = "mojum.episode-fingerprint/1"
MAX_TRANSITIONS = 10_000
MAX_BYTES = 4_000_000
MAX_DEPTH = 24
MAX_NODES = 200_000
MAX_TEXT_BYTES = MAX_BYTES // 8
MAX_STRING_CHARS = 4096
MAX_MAPPING_ITEMS = 256
MAX_ARRAY_ITEMS = 1024

class FingerprintError(ValueError):
    """Invalid or oversized public synthetic episode."""


def _add_text_budget(value: str, budget: list[int]) -> None:
    if len(value) > budget[0]:
        raise FingerprintError("text byte budget exceeded")
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as exc:
        raise FingerprintError("text is not valid UTF-8") from exc
    if size > budget[0]:
        raise FingerprintError("text byte budget exceeded")
    budget[0] -= size


def _normalize(value, depth: int, budget: list[int], seen: set[int]):
    budget[1] += 1
    if budget[1] > MAX_NODES or depth > MAX_DEPTH:
        raise FingerprintError("structural budget exceeded")
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        if len(value) > MAX_STRING_CHARS:
            raise FingerprintError("string too long")
        _add_text_budget(value, budget)
        return value
    if type(value) is int:
        if value.bit_length() > 64:
            raise FingerprintError("integer too large")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise FingerprintError("non-finite float")
        return value
    if type(value) is dict:
        if len(value) > MAX_MAPPING_ITEMS:
            raise FingerprintError("mapping too large")
        identity = id(value)
        if identity in seen:
            raise FingerprintError("cyclic or aliased containers are not allowed")
        seen.add(identity)
        keys = tuple(dict.keys(value))
        for key in keys:
            if type(key) is not str or not key or len(key) > 128:
                raise FingerprintError("invalid mapping key")
            _add_text_budget(key, budget)
        normalized = {}
        for key in sorted(keys):
            normalized[key] = _normalize(dict.__getitem__(value, key), depth + 1, budget, seen)
        return normalized
    if type(value) in (list, tuple):
        if len(value) > MAX_ARRAY_ITEMS:
            raise FingerprintError("array too long")
        identity = id(value)
        if identity in seen:
            raise FingerprintError("cyclic or aliased containers are not allowed")
        seen.add(identity)
        return [_normalize(item, depth + 1, budget, seen) for item in value]
    raise FingerprintError("unsupported value type")


def fingerprint_episode(transitions, *, episode_id: str) -> dict:
    """Produce a content witness without echoing observation/action values."""
    if type(episode_id) is not str or not 1 <= len(episode_id) <= 128:
        raise FingerprintError("invalid episode id")
    try:
        episode_id_bytes = episode_id.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise FingerprintError("episode id is not valid UTF-8") from exc
    if type(transitions) not in (list, tuple) or not 1 <= len(transitions) <= MAX_TRANSITIONS:
        raise FingerprintError("invalid transition count")

    h = hashlib.sha256()
    total = 0
    budget = [MAX_TEXT_BYTES, 0]
    required = {"tick", "observation", "action", "reward", "terminated", "truncated"}
    for index, transition in enumerate(transitions):
        if type(transition) is not dict or len(transition) != len(required):
            raise FingerprintError("invalid transition shape")
        keys = tuple(dict.keys(transition))
        if any(type(key) is not str for key in keys):
            raise FingerprintError("transition keys must be native strings")
        if set(keys) != required:
            raise FingerprintError("invalid transition shape")

        tick = dict.__getitem__(transition, "tick")
        terminated = dict.__getitem__(transition, "terminated")
        truncated = dict.__getitem__(transition, "truncated")
        reward = dict.__getitem__(transition, "reward")
        if type(tick) is not int or tick != index:
            raise FingerprintError("ticks must be contiguous starting at zero")
        if type(terminated) is not bool or type(truncated) is not bool:
            raise FingerprintError("terminal flags must be booleans")
        if type(reward) is int:
            if reward.bit_length() > 64:
                raise FingerprintError("reward integer is too large")
        elif type(reward) is float:
            if not math.isfinite(reward):
                raise FingerprintError("reward must be finite numeric")
        else:
            raise FingerprintError("reward must be finite numeric")
        if index < len(transitions) - 1 and (terminated or truncated):
            raise FingerprintError("terminal transition must be last")

        normalized = _normalize(transition, 0, budget, set())
        try:
            canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True, allow_nan=False).encode("ascii")
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise FingerprintError("transition cannot be canonically encoded") from exc
        total += len(canonical) + 8
        if total > MAX_BYTES:
            raise FingerprintError("episode byte budget exceeded")
        h.update(len(canonical).to_bytes(8, "big"))
        h.update(canonical)

    domain = hashlib.sha256(SCHEMA.encode("ascii") + b"\0" + episode_id_bytes).digest()
    return {"schema": SCHEMA, "episode_id": episode_id, "transition_count": len(transitions),
            "sha256": hashlib.sha256(domain + h.digest()).hexdigest()}
