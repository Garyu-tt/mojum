"""Bounded, read-only bitemporal conflict audit for public synthetic facts.

This module is deliberately detached from the canonical Noosphere journal. It
accepts caller-supplied PUBLIC_SYNTHETIC facts and reports contradictory
values whose half-open valid-time intervals overlap. It neither resolves nor
persists conflicts.
"""

from __future__ import annotations

import hashlib
import json
import math

SCHEMA = "mojum.noosphere.validity-audit/1"
_MAX_FACTS = 4096
_MAX_FACT_KEY = 256
_MAX_EVENT_ID = 256
_MAX_VALUE_BYTES = 16 * 1024
_MAX_TOTAL_VALUE_BYTES = 4 * 1024 * 1024
_MAX_VALUE_NODES = 100_000
_MAX_VALUE_DEPTH = 32
_MAX_WIRE_BYTES = 8 * 1024 * 1024
_MAX_PAIR_CHECKS = 250_000
_MAX_CONFLICTS = 25_000
_MAX_TIMESTAMP = 2**63 - 1
_EXPECTED_FACT_FIELDS = frozenset({
    "event_id", "fact_key", "visibility", "provenance",
    "valid_from_ns", "valid_to_ns", "value",
})


class ValidityAuditError(ValueError):
    """Input is malformed, ambiguous, or exceeds an explicit audit budget."""


def _utf8(value: str, field: str, max_length: int) -> None:
    if type(value) is not str or not value or len(value) > max_length:
        raise ValidityAuditError(f"{field} must be a nonempty native string of <= {max_length} characters")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValidityAuditError(f"{field} must be valid UTF-8") from exc


def _timestamp(value, field: str, *, allow_none: bool = False):
    if allow_none and value is None:
        return
    if type(value) is not int or not 0 <= value <= _MAX_TIMESTAMP:
        raise ValidityAuditError(f"{field} must be an integer in [0, 2**63-1]")


def _validate_json_value(value):
    """Validate a detached JSON tree before canonical encoding; iterative/bounded."""
    stack = [(value, 0)]
    seen_containers = set()
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_VALUE_NODES:
            raise ValidityAuditError("value node budget exceeded")
        if depth > _MAX_VALUE_DEPTH:
            raise ValidityAuditError("value nesting budget exceeded")
        kind = type(item)
        if item is None or kind is bool:
            continue
        if kind is int:
            if not -(2**63) <= item <= _MAX_TIMESTAMP:
                raise ValidityAuditError("value integer outside signed 64-bit range")
            continue
        if kind is float:
            if not math.isfinite(item):
                raise ValidityAuditError("value contains a non-finite number")
            continue
        if kind is str:
            if len(item) > 4096:
                raise ValidityAuditError("value string exceeds 4096 characters")
            try:
                item.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise ValidityAuditError("value contains invalid UTF-8") from exc
            continue
        if kind is list:
            identity = id(item)
            if identity in seen_containers:
                raise ValidityAuditError("value contains a cycle or shared container")
            # Reject oversized fan-out before extending the traversal stack.
            # nodes counts processed values; len(stack) counts already queued
            # values, so this bounds both traversal work and temporary memory.
            if len(item) > _MAX_VALUE_NODES - nodes - len(stack):
                raise ValidityAuditError("value node budget exceeded")
            seen_containers.add(identity)
            for child in reversed(item):
                stack.append((child, depth + 1))
            continue
        if kind is dict:
            identity = id(item)
            if identity in seen_containers:
                raise ValidityAuditError("value contains a cycle or shared container")
            # Check cardinality before iteration and avoid materializing a
            # second full list of entries for caller-supplied dictionaries.
            if len(item) > _MAX_VALUE_NODES - nodes - len(stack):
                raise ValidityAuditError("value node budget exceeded")
            seen_containers.add(identity)
            for key, child in dict.items(item):
                if type(key) is not str or len(key) > 256:
                    raise ValidityAuditError("value object keys must be native strings of <= 256 characters")
                try:
                    key.encode("utf-8", errors="strict")
                except UnicodeEncodeError as exc:
                    raise ValidityAuditError("value object key contains invalid UTF-8") from exc
                stack.append((child, depth + 1))
            continue
        raise ValidityAuditError("value must contain only native JSON types")

    try:
        wire = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeEncodeError) as exc:
        raise ValidityAuditError("value is not canonical UTF-8 JSON") from exc
    if len(wire) > _MAX_VALUE_BYTES:
        raise ValidityAuditError("individual value byte budget exceeded")
    return wire


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if type(key) is not str:
            raise ValidityAuditError("JSON object keys must be strings")
        if key in result:
            raise ValidityAuditError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValidityAuditError("non-finite JSON constants are forbidden")


def _parse_int(raw):
    # Keep Python's arbitrary precision parser from creating unbounded integers.
    if len(raw.lstrip("-")) > 19:
        raise ValidityAuditError("JSON integer exceeds signed 64-bit budget")
    value = int(raw)
    if not -(2**63) <= value <= _MAX_TIMESTAMP:
        raise ValidityAuditError("JSON integer exceeds signed 64-bit budget")
    return value


def _parse_float(raw):
    value = float(raw)
    if not math.isfinite(value):
        raise ValidityAuditError("non-finite JSON number is forbidden")
    return value


def _canonical_digest(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def audit_validity(facts):
    """Return deterministic conflict metadata for bounded PUBLIC_SYNTHETIC facts.

    Each fact has exactly the event_id, fact_key, visibility, provenance,
    valid_from_ns, valid_to_ns and value fields. Validity is half-open:
    [valid_from_ns, valid_to_ns); a None end is unbounded. Identical values
    may overlap harmlessly.
    """
    if type(facts) not in (list, tuple) or len(facts) > _MAX_FACTS:
        raise ValidityAuditError("facts must be a list/tuple of at most 4096 items")

    identities = set()
    groups = {}
    total_value_bytes = 0
    for index, fact in enumerate(facts):
        if type(fact) is not dict:
            raise ValidityAuditError(f"fact {index} must be a native object")
        # This fixed seven-field schema must be cardinality-checked before
        # traversing caller-controlled keys or allocating a comparison set.
        if len(fact) != len(_EXPECTED_FACT_FIELDS):
            raise ValidityAuditError(f"fact {index} has missing or unknown fields")
        keys = tuple(dict.keys(fact))
        if any(type(key) is not str for key in keys):
            raise ValidityAuditError(f"fact {index} has a non-native object key")
        if set(keys) != _EXPECTED_FACT_FIELDS:
            raise ValidityAuditError(f"fact {index} has missing or unknown fields")

        event_id = fact["event_id"]
        fact_key = fact["fact_key"]
        _utf8(event_id, "event_id", _MAX_EVENT_ID)
        _utf8(fact_key, "fact_key", _MAX_FACT_KEY)
        if event_id in identities:
            raise ValidityAuditError("duplicate event_id")
        identities.add(event_id)
        visibility = fact["visibility"]
        provenance = fact["provenance"]
        if type(visibility) is not str or visibility != "PUBLIC":
            raise ValidityAuditError("only PUBLIC facts are eligible")
        if type(provenance) is not str or provenance != "PUBLIC_SYNTHETIC":
            raise ValidityAuditError("only explicitly PUBLIC_SYNTHETIC facts are eligible")

        start = fact["valid_from_ns"]
        end = fact["valid_to_ns"]
        _timestamp(start, "valid_from_ns")
        _timestamp(end, "valid_to_ns", allow_none=True)
        if end is not None and end <= start:
            raise ValidityAuditError("valid_to_ns must be greater than valid_from_ns")

        value_wire = _validate_json_value(fact["value"])
        total_value_bytes += len(value_wire)
        if total_value_bytes > _MAX_TOTAL_VALUE_BYTES:
            raise ValidityAuditError("total value byte budget exceeded")
        item = {
            "event_id": event_id,
            "fact_key": fact_key,
            "start": start,
            "end": end,
            "value_sha256": hashlib.sha256(value_wire).hexdigest(),
        }
        groups.setdefault(fact_key, []).append(item)

    conflicts = []
    pair_checks = 0
    for fact_key in sorted(groups):
        ordered = sorted(groups[fact_key], key=lambda x: (x["start"], x["event_id"]))
        active = []
        key_digest = hashlib.sha256(fact_key.encode("utf-8")).hexdigest()
        for current in ordered:
            # Half-open intervals expire when their end equals the next start.
            active = [old for old in active
                      if old["end"] is None or old["end"] > current["start"]]
            for old in active:
                pair_checks += 1
                if pair_checks > _MAX_PAIR_CHECKS:
                    raise ValidityAuditError("overlap comparison budget exceeded")
                if old["value_sha256"] == current["value_sha256"]:
                    continue
                if len(conflicts) >= _MAX_CONFLICTS:
                    raise ValidityAuditError("conflict output budget exceeded")
                overlap_start = max(old["start"], current["start"])
                if old["end"] is None:
                    overlap_end = current["end"]
                elif current["end"] is None:
                    overlap_end = old["end"]
                else:
                    overlap_end = min(old["end"], current["end"])
                left, right = sorted((old, current), key=lambda x: x["event_id"])
                conflicts.append({
                    "fact_key_sha256": key_digest,
                    "left_event_id": left["event_id"],
                    "right_event_id": right["event_id"],
                    "overlap_from_ns": overlap_start,
                    "overlap_to_ns": overlap_end,
                    "left_value_sha256": left["value_sha256"],
                    "right_value_sha256": right["value_sha256"],
                })
            active.append(current)

    conflicts.sort(key=lambda x: (
        x["fact_key_sha256"], x["overlap_from_ns"], x["left_event_id"],
        x["right_event_id"], x["overlap_to_ns"] if x["overlap_to_ns"] is not None else _MAX_TIMESTAMP,
    ))
    report = {
        "schema": SCHEMA,
        "facts_checked": len(facts),
        "conflict_count": len(conflicts),
        "pair_checks": pair_checks,
        "conflicts": conflicts,
        "scope": "PUBLIC_SYNTHETIC_ONLY",
        "resolution": "NOT_PERFORMED",
    }
    report["sha256"] = _canonical_digest(report)
    return report


def audit_json(wire):
    """Parse a strict versioned JSON envelope and run audit_validity."""
    if type(wire) is bytes:
        if len(wire) > _MAX_WIRE_BYTES:
            raise ValidityAuditError("wire byte budget exceeded")
        try:
            wire = wire.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValidityAuditError("wire must be valid UTF-8") from exc
    elif type(wire) is str:
        try:
            wire_bytes = wire.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValidityAuditError("wire must be valid UTF-8") from exc
        if len(wire_bytes) > _MAX_WIRE_BYTES:
            raise ValidityAuditError("wire byte budget exceeded")
    else:
        raise ValidityAuditError("wire must be exact str or bytes")

    try:
        envelope = json.loads(
            wire, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
            parse_int=_parse_int, parse_float=_parse_float,
        )
    except ValidityAuditError:
        raise
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValidityAuditError("invalid audit JSON") from exc
    if (type(envelope) is not dict or len(envelope) != 2
            or set(dict.keys(envelope)) != {"schema", "facts"}
            or envelope["schema"] != SCHEMA):
        raise ValidityAuditError("unsupported audit envelope")
    return audit_validity(envelope["facts"])
