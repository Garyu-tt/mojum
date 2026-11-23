"""Offline, opt-in, privacy-safe synthetic trace envelope admission.

No transport, persistence, credentials or private telemetry access.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

SCHEMA = "mojum.trace-envelope/1"
_ALLOWED = frozenset({"schema", "provenance", "consent", "trace_id", "phase", "outcome", "duration_ms", "attempt"})
_PHASES = frozenset({"admission", "patch", "test", "review", "acceptance"})
_OUTCOMES = frozenset({"ok", "failed", "blocked", "skipped"})
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{7,63}\Z")


class TraceEnvelopeError(ValueError):
    """Rejected trace envelope. Never includes input values."""


@dataclass(frozen=True)
class AdmittedTrace:
    """Detached, sanitized trace record and consistency digest."""

    fields: Mapping[str, object]
    sha256: str


def admit_trace(value: object) -> AdmittedTrace:
    """Admit only explicit synthetic metadata, without echoing rejected input.

    Exact built-in containers and keys are checked before hashing/comparison,
    preventing hostile string subclasses from executing during schema checks.
    The digest is a consistency checksum, NOT a signature.
    """
    if type(value) is not dict:
        raise TraceEnvelopeError("expected plain object")
    if any(type(key) is not str or len(key) > 32 for key in value):
        raise TraceEnvelopeError("invalid key type or length")
    if len(value) != len(_ALLOWED) or set(value) != _ALLOWED:
        raise TraceEnvelopeError("invalid field set")
    if type(value["schema"]) is not str or value["schema"] != SCHEMA:
        raise TraceEnvelopeError("invalid schema")
    if type(value["provenance"]) is not str or value["provenance"] != "PUBLIC_SYNTHETIC":
        raise TraceEnvelopeError("invalid provenance")
    if type(value["consent"]) is not bool or value["consent"] is not True:
        raise TraceEnvelopeError("explicit consent required")
    if type(value["trace_id"]) is not str or _ID.fullmatch(value["trace_id"]) is None:
        raise TraceEnvelopeError("invalid trace id")
    if type(value["phase"]) is not str or value["phase"] not in _PHASES:
        raise TraceEnvelopeError("invalid phase")
    if type(value["outcome"]) is not str or value["outcome"] not in _OUTCOMES:
        raise TraceEnvelopeError("invalid outcome")
    if type(value["duration_ms"]) is not int or not 0 <= value["duration_ms"] <= 86_400_000:
        raise TraceEnvelopeError("invalid duration")
    if type(value["attempt"]) is not int or not 1 <= value["attempt"] <= 1000:
        raise TraceEnvelopeError("invalid attempt")
    clean = {key: value[key] for key in sorted(_ALLOWED)}
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return AdmittedTrace(MappingProxyType(clean), hashlib.sha256(canonical).hexdigest())
