"""Privacy-safe, opt-in trace metadata sanitizer (stdlib only).

This module deliberately accepts a tiny typed vocabulary instead of trying to
redact arbitrary application payloads. Unknown fields and values fail closed.
It does not send telemetry or inspect source records.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SCHEMA = "mojum.trace-attributes/1"
MAX_ATTRIBUTES = 24
MAX_INTEGER = 2**63 - 1

# Explicit bounds reject oversized values in O(1) before regex scanning.
_STRING_MAX_LENGTHS = {
    "trace_id": 64,
    "lease_id": 64,
    "head_sha": 64,
    "event": 48,
    "component": 32,
    "operation": 48,
    "outcome": 8,
    "status": 10,
    "reason_code": 48,
}
_STRING_PATTERNS = {
    "trace_id": re.compile(r"[0-9a-f]{32,64}\Z"),
    "lease_id": re.compile(r"[0-9a-f]{32,64}\Z"),
    "head_sha": re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z"),
    "event": re.compile(r"[a-z][a-z0-9_.-]{0,47}\Z"),
    "component": re.compile(r"[a-z][a-z0-9_.-]{0,31}\Z"),
    "operation": re.compile(r"[a-z][a-z0-9_.-]{0,47}\Z"),
    "outcome": re.compile(r"(?:success|failure|skipped|deferred|rejected)\Z"),
    "status": re.compile(r"(?:ok|error|pending|blocked|complete|incomplete)\Z"),
    "reason_code": re.compile(r"[a-z][a-z0-9_.-]{0,47}\Z"),
}
_INTEGER_FIELDS = {
    "fencing_epoch", "pr_number", "duration_ms", "latency_ms",
    "input_tokens", "output_tokens", "cost_microusd",
}
_ALLOWED_FIELDS = frozenset(_STRING_PATTERNS) | frozenset(_INTEGER_FIELDS)


class TraceSanitizationError(ValueError):
    """Raised when trace attributes are not safe for the public trace schema."""


def sanitize_trace_attributes(attributes: Any) -> dict[str, str | int]:
    """Validate and detach a bounded mapping of explicitly allowed metadata."""
    if type(attributes) is not dict:
        raise TraceSanitizationError("attributes must be an exact dict")
    if len(attributes) > MAX_ATTRIBUTES:
        raise TraceSanitizationError("too many trace attributes")

    clean: dict[str, str | int] = {}
    for key, value in attributes.items():
        if type(key) is not str or key not in _ALLOWED_FIELDS:
            raise TraceSanitizationError("unknown or unsafe trace attribute")
        if key in _STRING_PATTERNS:
            if type(value) is not str:
                raise TraceSanitizationError("invalid trace identifier or enum")
            if len(value) > _STRING_MAX_LENGTHS[key]:
                raise TraceSanitizationError("trace string exceeds its field length limit")
            if not _STRING_PATTERNS[key].fullmatch(value):
                raise TraceSanitizationError("invalid trace identifier or enum")
            clean[key] = value
            continue
        if type(value) is not int or value < 0 or value > MAX_INTEGER:
            raise TraceSanitizationError("invalid trace counter")
        if key == "pr_number" and value == 0:
            raise TraceSanitizationError("pr_number must be positive")
        clean[key] = value

    return {key: clean[key] for key in sorted(clean)}


def trace_attributes_digest(attributes: Any) -> str:
    """Return a stable SHA-256 digest of validated canonical trace metadata."""
    clean = sanitize_trace_attributes(attributes)
    wire = json.dumps(
        {"schema": SCHEMA, "attributes": clean},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(wire).hexdigest()
