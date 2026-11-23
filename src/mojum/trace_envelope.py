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

    Exact built-in types prevent bool-as-int and hostile mapping subclasses.
    The digest is a consistency checksum, NOT a signature.
    """
    if type(value) is not dict:
        raise TraceEnvelopeError("expected plain object")
    if len(value) != len(_ALLOWED):
        raise TraceEnvelopeError("invalid field count")

    # Do not hash, compare, or look up caller-supplied keys until their exact
    # native type and bounded length have been established.
    clean_input: dict[str, object] = {}
    for key, item in dict.items(value):
        if type(key) is not str or not key or len(key) > 32:
            raise TraceEnvelopeError("invalid key type or length")
        clean_input[key] = item
    if len(clean_input) != len(_ALLOWED) or set(clean_input) != _ALLOWED:
        raise TraceEnvelopeError("invalid field set")

    if type(clean_input["schema"]) is not str or clean_input["schema"] != SCHEMA:
        raise TraceEnvelopeError("invalid schema")
    if type(clean_input["provenance"]) is not str or clean_input["provenance"] != "PUBLIC_SYNTHETIC":
        raise TraceEnvelopeError("invalid provenance")
    if type(clean_input["consent"]) is not bool or clean_input["consent"] is not True:
        raise TraceEnvelopeError("explicit consent required")
    if type(clean_input["trace_id"]) is not str or _ID.fullmatch(clean_input["trace_id"]) is None:
        raise TraceEnvelopeError("invalid trace id")
    if type(clean_input["phase"]) is not str or clean_input["phase"] not in _PHASES:
        raise TraceEnvelopeError("invalid phase")
    if type(clean_input["outcome"]) is not str or clean_input["outcome"] not in _OUTCOMES:
        raise TraceEnvelopeError("invalid outcome")
    if type(clean_input["duration_ms"]) is not int or not 0 <= clean_input["duration_ms"] <= 86_400_000:
        raise TraceEnvelopeError("invalid duration")
    if type(clean_input["attempt"]) is not int or not 1 <= clean_input["attempt"] <= 1000:
        raise TraceEnvelopeError("invalid attempt")
    clean = {key: clean_input[key] for key in sorted(_ALLOWED)}
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return AdmittedTrace(MappingProxyType(clean), hashlib.sha256(canonical).hexdigest())
