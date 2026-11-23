"""Strict, offline privacy boundary for Mojum operational trace metadata.

This module deliberately accepts metadata only. It has no network client and
never attempts to redact arbitrary prompt/source/journal payloads: unknown
fields are rejected so sensitive data cannot accidentally cross the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Mapping

SCHEMA = "mojum.trace-envelope.v1"
MAX_FIELDS = 12
MAX_STRING_LENGTH = 128
_SHA_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_TRACE_ID_RE = re.compile(r"[0-9a-f]{32,64}\Z")

_ALLOWED_EVENTS = frozenset({
    "provider.admitted",
    "provider.rejected",
    "patch.started",
    "patch.completed",
    "tests.started",
    "tests.completed",
    "pr.opened",
    "qa.completed",
    "ci.completed",
})

_ALLOWED_KEYS = frozenset({
    "schema", "trace_id", "lease_id", "fencing_token", "pr_number",
    "head_sha", "event_type", "timestamp_ms", "latency_ms",
    "input_tokens", "output_tokens", "cost_usd",
})


class TracePrivacyError(ValueError):
    """Trace metadata is malformed, excessive, or outside the safe allowlist."""


@dataclass(frozen=True, slots=True)
class TraceEnvelope:
    """Validated scalar-only metadata; no user-controlled nested objects."""

    schema: str
    trace_id: str
    lease_id: str
    fencing_token: int
    pr_number: int
    head_sha: str
    event_type: str
    timestamp_ms: int
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a fresh JSON-compatible copy, never internal mutable state."""
        return {
            key: value
            for key, value in (
                ("schema", self.schema),
                ("trace_id", self.trace_id),
                ("lease_id", self.lease_id),
                ("fencing_token", self.fencing_token),
                ("pr_number", self.pr_number),
                ("head_sha", self.head_sha),
                ("event_type", self.event_type),
                ("timestamp_ms", self.timestamp_ms),
                ("latency_ms", self.latency_ms),
                ("input_tokens", self.input_tokens),
                ("output_tokens", self.output_tokens),
                ("cost_usd", self.cost_usd),
            )
            if value is not None
        }


def _required_text(raw: Mapping[str, object], key: str, pattern: re.Pattern[str]) -> str:
    value = raw.get(key)
    if type(value) is not str or len(value) > MAX_STRING_LENGTH:
        raise TracePrivacyError(f"{key} must be a bounded string")
    if pattern.fullmatch(value) is None:
        raise TracePrivacyError(f"{key} has an invalid format")
    return value


def _required_int(raw: Mapping[str, object], key: str, minimum: int) -> int:
    value = raw.get(key)
    if type(value) is not int or value < minimum:
        raise TracePrivacyError(f"{key} must be an integer >= {minimum}")
    return value


def _optional_int(raw: Mapping[str, object], key: str) -> int | None:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if type(value) is not int or value < 0:
        raise TracePrivacyError(f"{key} must be a non-negative integer")
    return value


def _optional_cost(raw: Mapping[str, object]) -> float | None:
    if "cost_usd" not in raw or raw["cost_usd"] is None:
        return None
    value = raw["cost_usd"]
    if type(value) not in (int, float):
        raise TracePrivacyError("cost_usd must be a finite non-negative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise TracePrivacyError("cost_usd must be a finite non-negative number")
    return result


def sanitize_trace_event(raw: object) -> TraceEnvelope:
    """Validate and copy an allowlisted metadata event; reject all payload fields.

    The caller must pass an already-minimized metadata mapping. This function
    intentionally does not recursively inspect or attempt to scrub arbitrary
    application objects, prompts, source text, PRIVATE events, or credentials.
    """
    if not isinstance(raw, Mapping):
        raise TracePrivacyError("trace event must be a mapping")
    if len(raw) > MAX_FIELDS:
        raise TracePrivacyError("trace event has too many fields")
    if any(type(key) is not str for key in raw):
        raise TracePrivacyError("trace keys must be strings")
    unknown = set(raw) - _ALLOWED_KEYS
    if unknown:
        # Do not echo rejected key names: callers may have supplied secret labels.
        raise TracePrivacyError("trace event contains fields outside the metadata allowlist")
    if raw.get("schema") != SCHEMA:
        raise TracePrivacyError("unsupported trace schema")

    event_type = raw.get("event_type")
    if type(event_type) is not str or event_type not in _ALLOWED_EVENTS:
        raise TracePrivacyError("event_type is not allowlisted")

    return TraceEnvelope(
        schema=SCHEMA,
        trace_id=_required_text(raw, "trace_id", _TRACE_ID_RE),
        lease_id=_required_text(raw, "lease_id", _ID_RE),
        fencing_token=_required_int(raw, "fencing_token", 1),
        pr_number=_required_int(raw, "pr_number", 1),
        head_sha=_required_text(raw, "head_sha", _SHA_RE),
        event_type=event_type,
        timestamp_ms=_required_int(raw, "timestamp_ms", 0),
        latency_ms=_optional_int(raw, "latency_ms"),
        input_tokens=_optional_int(raw, "input_tokens"),
        output_tokens=_optional_int(raw, "output_tokens"),
        cost_usd=_optional_cost(raw),
    )


class OfflineTraceSink:
    """Explicit local/offline no-op sink; it never buffers or transmits events."""

    __slots__ = ()

    def emit(self, event: object) -> bool:
        """Return False to report that no remote or local persistence occurred."""
        # Validate even in no-op mode so callers cannot mistake unsafe data for
        # an accepted trace event. The validated value is deliberately discarded.
        sanitize_trace_event(event)
        return False
