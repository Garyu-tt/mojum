"""Bounded offline audit of synthetic mesh peer capability revocations.

This module is an advisory consistency check over caller-supplied PUBLIC_SYNTHETIC
metadata. It does not authenticate peers, prove remote erasure, or grant authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any


SCHEMA = "mojum.mesh.revocation-audit/1"
VISIBILITY = "PUBLIC_SYNTHETIC"
MAX_INPUT_BYTES = 1_048_576
MAX_EVENTS = 10_000
MAX_QUERIES = 2_000
MAX_ID_LENGTH = 128
MAX_CAPABILITY_LENGTH = 64
MAX_SEQUENCE = (1 << 53) - 1


class RevocationAuditError(ValueError):
    """Raised when an untrusted revocation audit document is invalid."""


@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    peer_id: str
    capability: str
    allowed: bool
    reason: str
    event_sequence: int | None


@dataclass(frozen=True, slots=True)
class RevocationAudit:
    schema: str
    as_of_sequence: int
    decisions: tuple[CapabilityDecision, ...]
    event_count: int
    digest: str


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RevocationAuditError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise RevocationAuditError("non-finite JSON number")


def _exact_keys(value: dict[str, Any], expected: set[str], where: str) -> None:
    if type(value) is not dict or set(value) != expected:
        raise RevocationAuditError(f"{where} has an invalid shape")


def _text(value: Any, *, maximum: int, field: str) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise RevocationAuditError(f"{field} must be a non-empty bounded native string")
    if value.strip() != value or any(ord(ch) < 0x20 for ch in value):
        raise RevocationAuditError(f"{field} contains whitespace/control characters outside its contract")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise RevocationAuditError(f"{field} contains invalid Unicode scalar values") from exc
    return value


def _sequence(value: Any, *, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SEQUENCE:
        raise RevocationAuditError(f"{field} must be a bounded non-negative native integer")
    return value


def audit_peer_revocations(document: str) -> RevocationAudit:
    """Evaluate requested peer/capability pairs at one logical sequence.

    Expected JSON keys: schema, visibility, as_of_sequence, events, queries.
    An event is {event_id, peer_id, capability, kind, sequence}; a query is
    {peer_id, capability}. Events are ordered by increasing sequence per
    (peer_id, capability). A later explicit grant can restore a revoked right.
    """
    if type(document) is not str:
        raise RevocationAuditError("document must be a native JSON string")
    # UTF-8 byte length is never smaller than the code-point count. Reject
    # obviously oversized input before allocating an encoded copy; the byte
    # check below still handles multibyte text at the boundary.
    if len(document) > MAX_INPUT_BYTES:
        raise RevocationAuditError("document exceeds the input byte budget")
    try:
        encoded = document.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise RevocationAuditError("document is not valid UTF-8 text") from exc
    if len(encoded) > MAX_INPUT_BYTES:
        raise RevocationAuditError("document exceeds the input byte budget")

    try:
        payload = json.loads(
            document,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
    except RevocationAuditError:
        raise
    except (ValueError, TypeError, RecursionError) as exc:
        raise RevocationAuditError("malformed JSON document") from exc

    if type(payload) is not dict:
        raise RevocationAuditError("root must be a native object")
    _exact_keys(payload, {"schema", "visibility", "as_of_sequence", "events", "queries"}, "root")
    if payload["schema"] != SCHEMA or type(payload["schema"]) is not str:
        raise RevocationAuditError("unsupported schema")
    if payload["visibility"] != VISIBILITY or type(payload["visibility"]) is not str:
        raise RevocationAuditError("explicit PUBLIC_SYNTHETIC visibility is required")

    as_of = _sequence(payload["as_of_sequence"], field="as_of_sequence")
    events = payload["events"]
    queries = payload["queries"]
    if type(events) is not list or len(events) > MAX_EVENTS:
        raise RevocationAuditError("events must be a bounded native list")
    if type(queries) is not list or not 1 <= len(queries) <= MAX_QUERIES:
        raise RevocationAuditError("queries must be a non-empty bounded native list")

    normalized_events: list[tuple[str, str, str, str, int]] = []
    event_ids: set[str] = set()
    positions: set[tuple[str, str, int]] = set()
    last_by_pair: dict[tuple[str, str], int] = {}
    for index, event in enumerate(events):
        _exact_keys(event, {"event_id", "peer_id", "capability", "kind", "sequence"}, f"events[{index}]")
        event_id = _text(event["event_id"], maximum=MAX_ID_LENGTH, field="event_id")
        peer_id = _text(event["peer_id"], maximum=MAX_ID_LENGTH, field="peer_id")
        capability = _text(event["capability"], maximum=MAX_CAPABILITY_LENGTH, field="capability")
        kind = event["kind"]
        if type(kind) is not str or kind not in {"grant", "revoke"}:
            raise RevocationAuditError(f"events[{index}].kind must be grant or revoke")
        sequence = _sequence(event["sequence"], field=f"events[{index}].sequence")
        if sequence == 0:
            raise RevocationAuditError("event sequence must be positive")
        if event_id in event_ids:
            raise RevocationAuditError("duplicate event_id")
        event_ids.add(event_id)
        position = (peer_id, capability, sequence)
        if position in positions:
            raise RevocationAuditError("duplicate peer/capability sequence")
        positions.add(position)
        pair = (peer_id, capability)
        previous = last_by_pair.get(pair)
        if previous is not None and sequence <= previous:
            raise RevocationAuditError("event sequences must increase per peer/capability in input order")
        last_by_pair[pair] = sequence
        normalized_events.append((event_id, peer_id, capability, kind, sequence))

    normalized_queries: list[tuple[str, str]] = []
    query_set: set[tuple[str, str]] = set()
    for index, query in enumerate(queries):
        _exact_keys(query, {"peer_id", "capability"}, f"queries[{index}]")
        pair = (
            _text(query["peer_id"], maximum=MAX_ID_LENGTH, field="peer_id"),
            _text(query["capability"], maximum=MAX_CAPABILITY_LENGTH, field="capability"),
        )
        if pair in query_set:
            raise RevocationAuditError("duplicate query")
        query_set.add(pair)
        normalized_queries.append(pair)

    by_pair: dict[tuple[str, str], tuple[str, int]] = {}
    for _, peer_id, capability, kind, sequence in normalized_events:
        if sequence <= as_of:
            by_pair[(peer_id, capability)] = (kind, sequence)

    decisions: list[CapabilityDecision] = []
    for peer_id, capability in sorted(normalized_queries):
        latest = by_pair.get((peer_id, capability))
        if latest is None:
            decisions.append(CapabilityDecision(peer_id, capability, False, "no_grant", None))
        elif latest[0] == "revoke":
            decisions.append(CapabilityDecision(peer_id, capability, False, "revoked", latest[1]))
        else:
            decisions.append(CapabilityDecision(peer_id, capability, True, "active_grant", latest[1]))

    canonical = {
        "schema": SCHEMA,
        "visibility": VISIBILITY,
        "as_of_sequence": as_of,
        "events": [
            {"event_id": event_id, "peer_id": peer_id, "capability": capability,
             "kind": kind, "sequence": sequence}
            for event_id, peer_id, capability, kind, sequence in sorted(
                normalized_events, key=lambda item: (item[1], item[2], item[4], item[0])
            )
        ],
        "queries": [
            {"peer_id": peer_id, "capability": capability}
            for peer_id, capability in sorted(normalized_queries)
        ],
    }
    canonical_bytes = json.dumps(
        canonical, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical_bytes).hexdigest()
    return RevocationAudit(SCHEMA, as_of, tuple(decisions), len(normalized_events), digest)
