"""Read-only explicit source-consent gate for Noosphere dataset projections."""

from __future__ import annotations

import hashlib
import json
import re

CONSENT_SCHEMA = "mojum.noosphere.source-consent/1"
DATASET_SCHEMA = "mojum.noosphere.dataset/1"
AUTHORIZED_SCHEMA = "mojum.noosphere.authorized-dataset/1"
MAX_GRANTS = 128
MAX_TOPICS = 64
MAX_EVENTS = 10_000
MAX_WIRE_BYTES = 16 * 1024 * 1024
MAX_TREE_NODES = 200_000
MAX_TREE_DEPTH = 64
_VISIBILITY = frozenset({"PUBLIC", "RESTRICTED", "PRIVATE"})
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_GRANT_KEYS = {
    "schema", "consent_id", "agent_id", "source", "source_revision",
    "topics", "allowed_visibility", "valid_from_ns", "valid_to_ns",
    "purpose", "granted_by",
}
_EVENT_KEYS = {
    "schema", "event_id", "agent_id", "topic", "observed_at_ns",
    "recorded_at_ns", "valid_from_ns", "valid_to_ns", "visibility",
    "source", "source_revision", "payload",
}
_PROJECTION_KEYS = {"schema", "agent_id", "filters", "items"}
_FILTER_KEYS = {
    "topic", "source", "as_of_recorded_ns", "valid_at_ns",
    "include_nonpublic", "limit",
}


class ConsentError(ValueError):
    """Invalid, ambiguous, expired, or insufficient consent evidence."""


def _text(value, field, maximum=256):
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ConsentError(f"{field} must be nonempty text of at most {maximum} characters")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ConsentError(f"{field} must be valid UTF-8") from exc
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConsentError(f"{field} contains control characters")
    return value


def _integer(value, field):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ConsentError(f"{field} must be an exact nonnegative 63-bit integer")
    return value


def _json_string_size(value):
    """Count the exact UTF-8 bytes emitted for one JSON string, without encoding it."""
    if len(value) > MAX_WIRE_BYTES:
        raise ConsentError("input exceeds the maximum canonical JSON size")
    size = 2
    short_escapes = {8, 9, 10, 12, 13}
    for char in value:
        code = ord(char)
        if code in (34, 92):
            size += 2
        elif code < 32:
            size += 2 if code in short_escapes else 6
        elif 0xD800 <= code <= 0xDFFF:
            raise ConsentError("value contains invalid Unicode")
        elif code <= 0x7F:
            size += 1
        elif code <= 0x7FF:
            size += 2
        elif code <= 0xFFFF:
            size += 3
        else:
            size += 4
        if size > MAX_WIRE_BYTES:
            raise ConsentError("input exceeds the maximum canonical JSON size")
    return size


def _preflight_json(value):
    """Bound native JSON shape and encoded size before allocating serialized bytes."""
    stack = [(value, 0)]
    nodes = 0
    encoded_size = 0

    def add_size(amount):
        nonlocal encoded_size
        encoded_size += amount
        if encoded_size > MAX_WIRE_BYTES:
            raise ConsentError("input exceeds the maximum canonical JSON size")

    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 200_000:
            raise ConsentError("input exceeds the maximum JSON node count")
        if depth > 64:
            raise ConsentError("input exceeds the maximum JSON nesting depth")

        kind = type(item)
        if kind is dict:
            if nodes + len(item) > 200_000:
                raise ConsentError("input exceeds the maximum JSON node count")
            add_size(2 + max(0, len(item) - 1))
            for key, child in item.items():
                if type(key) is not str:
                    raise ConsentError("JSON object keys must be exact strings")
                add_size(_json_string_size(key) + 1)
                stack.append((child, depth + 1))
        elif kind is list:
            if nodes + len(item) > 200_000:
                raise ConsentError("input exceeds the maximum JSON node count")
            add_size(2 + max(0, len(item) - 1))
            for child in item:
                stack.append((child, depth + 1))
        elif kind is str:
            add_size(_json_string_size(item))
        elif kind is int:
            if item.bit_length() > MAX_WIRE_BYTES * 4:
                raise ConsentError("integer exceeds the canonical JSON size budget")
            try:
                add_size(len(str(item)))
            except ValueError as exc:
                raise ConsentError("integer exceeds the canonical JSON size budget") from exc
        elif kind is float:
            import math
            if not math.isfinite(item):
                raise ConsentError("value is not canonical UTF-8 JSON")
            add_size(len(repr(item)))
        elif kind is bool:
            add_size(4 if item else 5)
        elif item is None:
            add_size(4)
        else:
            raise ConsentError("value contains a non-native JSON type")


def _check_native_json_tree(value):
    """Bound an untrusted JSON tree before equality, hashing, or serialization."""
    stack = [(value, 0)]
    seen = set()
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_TREE_NODES or depth > MAX_TREE_DEPTH:
            raise ConsentError("JSON structure exceeds its depth or node budget")
        kind = type(item)
        if kind is dict:
            identity = id(item)
            if identity in seen:
                raise ConsentError("JSON input must be a tree without cycles or shared containers")
            seen.add(identity)
            keys = tuple(dict.keys(item))
            if len(keys) > MAX_TREE_NODES - nodes:
                raise ConsentError("JSON object exceeds the node budget")
            if any(type(key) is not str for key in keys):
                raise ConsentError("JSON object keys must be native strings")
            for key in keys:
                if len(key) > MAX_WIRE_BYTES:
                    raise ConsentError("JSON object key exceeds the byte budget")
                try:
                    key.encode("utf-8", errors="strict")
                except UnicodeEncodeError as exc:
                    raise ConsentError("JSON object key must be valid UTF-8") from exc
                stack.append((item[key], depth + 1))
        elif kind is list:
            identity = id(item)
            if identity in seen:
                raise ConsentError("JSON input must be a tree without cycles or shared containers")
            seen.add(identity)
            if len(item) > MAX_TREE_NODES - nodes:
                raise ConsentError("JSON array exceeds the node budget")
            for child in item:
                stack.append((child, depth + 1))
        elif kind is str:
            if len(item) > MAX_WIRE_BYTES:
                raise ConsentError("JSON text exceeds the byte budget")
            try:
                item.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise ConsentError("JSON text must be valid UTF-8") from exc
        elif kind is int:
            if not -(2**63) <= item <= 2**63 - 1:
                raise ConsentError("JSON integer exceeds the signed 64-bit limit")
        elif kind is float:
            if not math.isfinite(item):
                raise ConsentError("JSON numbers must be finite")
        elif kind not in (bool, type(None)):
            raise ConsentError("input must contain native JSON values")


def _canonical(value):
    _preflight_json(value)
    try:
        wire = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
        encoded = wire.encode("utf-8", errors="strict")
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        raise ConsentError("value is not canonical UTF-8 JSON") from exc
    if len(encoded) > MAX_WIRE_BYTES:
        raise ConsentError("input exceeds the maximum canonical JSON size")
    return wire


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _exact_mapping(value, keys, field):
    if type(value) is not dict:
        raise ConsentError(f"{field} must contain exactly the documented fields")
    native_keys = tuple(dict.keys(value))
    if any(type(key) is not str for key in native_keys):
        raise ConsentError(f"{field} keys must be native strings")
    if frozenset(native_keys) != keys:
        raise ConsentError(f"{field} must contain exactly the documented fields")


def _sorted_text_list(value, field, maximum):
    if type(value) is not list or not 1 <= len(value) <= maximum:
        raise ConsentError(f"{field} must contain 1..{maximum} entries")
    checked = [_text(item, f"{field} entry") for item in value]
    if checked != sorted(set(checked)):
        raise ConsentError(f"{field} must be sorted and contain no duplicates")
    return checked


def validate_grant(grant):
    """Validate and detach one explicit source-consent grant."""
    _check_native_json_tree(grant)
    _exact_mapping(grant, _GRANT_KEYS, "consent grant")
    if grant["schema"] != CONSENT_SCHEMA:
        raise ConsentError("unsupported consent grant schema")
    checked = {
        "schema": CONSENT_SCHEMA,
        "consent_id": _text(grant["consent_id"], "consent_id"),
        "agent_id": _text(grant["agent_id"], "agent_id"),
        "source": _text(grant["source"], "source"),
        "source_revision": _text(grant["source_revision"], "source_revision"),
        "topics": _sorted_text_list(grant["topics"], "topics", MAX_TOPICS),
        "allowed_visibility": _sorted_text_list(
            grant["allowed_visibility"], "allowed_visibility", len(_VISIBILITY)
        ),
        "valid_from_ns": _integer(grant["valid_from_ns"], "valid_from_ns"),
        "valid_to_ns": (None if grant["valid_to_ns"] is None
                        else _integer(grant["valid_to_ns"], "valid_to_ns")),
        "purpose": _text(grant["purpose"], "purpose"),
        "granted_by": _text(grant["granted_by"], "granted_by"),
    }
    if not set(checked["allowed_visibility"]) <= _VISIBILITY:
        raise ConsentError("allowed_visibility contains an unsupported level")
    if checked["valid_to_ns"] is not None and checked["valid_to_ns"] <= checked["valid_from_ns"]:
        raise ConsentError("valid_to_ns must be greater than valid_from_ns")
    _canonical(checked)
    return checked


def _validate_dataset(dataset):
    _check_native_json_tree(dataset)
    _exact_mapping(dataset, {"projection", "sha256"}, "dataset envelope")
    projection = dataset["projection"]
    _exact_mapping(projection, _PROJECTION_KEYS, "dataset projection")
    if projection["schema"] != DATASET_SCHEMA:
        raise ConsentError("unsupported dataset projection schema")
    agent_id = _text(projection["agent_id"], "dataset.agent_id")
    filters = projection["filters"]
    _exact_mapping(filters, _FILTER_KEYS, "dataset filters")
    for name in ("topic", "source"):
        if filters[name] is not None:
            _text(filters[name], f"filters.{name}")
    for name in ("as_of_recorded_ns", "valid_at_ns"):
        if filters[name] is not None:
            _integer(filters[name], f"filters.{name}")
    if type(filters["include_nonpublic"]) is not bool:
        raise ConsentError("filters.include_nonpublic must be boolean")
    limit = _integer(filters["limit"], "filters.limit")
    if not 1 <= limit <= MAX_EVENTS:
        raise ConsentError("filters.limit is outside the bounded dataset range")
    items = projection["items"]
    if type(items) is not list or len(items) > MAX_EVENTS or len(items) > limit:
        raise ConsentError("dataset items exceed the bounded event limit")
    supplied = dataset["sha256"]
    if type(supplied) is not str or not _SHA256.fullmatch(supplied):
        raise ConsentError("dataset sha256 must be lowercase SHA-256 hex")
    actual = _digest(projection)
    if supplied != actual:
        raise ConsentError("dataset projection digest mismatch")

    previous_seq = 0
    event_ids = set()
    detached = []
    for index, item in enumerate(items):
        _exact_mapping(item, {"seq", "event", "event_hash"}, f"items[{index}]")
        seq = _integer(item["seq"], f"items[{index}].seq")
        if seq <= previous_seq:
            raise ConsentError("sequence numbers must be positive and strictly increasing")
        previous_seq = seq
        event_hash = item["event_hash"]
        if type(event_hash) is not str or not _SHA256.fullmatch(event_hash):
            raise ConsentError(f"items[{index}].event_hash must be lowercase SHA-256 hex")
        event = item["event"]
        _exact_mapping(event, _EVENT_KEYS, f"items[{index}].event")
        if event["schema"] != "mojum.noosphere.event/1":
            raise ConsentError("unsupported Noosphere event schema")
        if _text(event["agent_id"], "event.agent_id") != agent_id:
            raise ConsentError("event agent_id differs from dataset owner")
        event_id = _text(event["event_id"], "event.event_id")
        if event_id in event_ids:
            raise ConsentError("duplicate event_id in dataset")
        event_ids.add(event_id)
        for name in ("topic", "source", "source_revision"):
            _text(event[name], f"event.{name}")
        for name in ("observed_at_ns", "recorded_at_ns", "valid_from_ns"):
            _integer(event[name], f"event.{name}")
        if event["valid_to_ns"] is not None:
            _integer(event["valid_to_ns"], "event.valid_to_ns")
            if event["valid_to_ns"] <= event["valid_from_ns"]:
                raise ConsentError("event validity window is empty or reversed")
        if event["recorded_at_ns"] < event["observed_at_ns"]:
            raise ConsentError("event recorded_at_ns precedes observed_at_ns")
        if type(event["visibility"]) is not str or event["visibility"] not in _VISIBILITY:
            raise ConsentError("event has unsupported visibility")
        if type(event["payload"]) is not dict:
            raise ConsentError("event payload must be an object")
        detached.append({
            "seq": seq, "event": json.loads(_canonical(event)),
            "event_hash": event_hash,
        })
    return agent_id, detached, actual


def authorize_dataset(dataset, grants, *, as_of_ns, purpose):
    """Filter an already chain-verified dataset using explicit exact-scope grants.

    This function checks projection shape and digest but does not independently
    verify the SQLite event hash chain or authenticate grant authorship.
    """
    timestamp = _integer(as_of_ns, "as_of_ns")
    requested_purpose = _text(purpose, "purpose")
    if type(grants) is not list or not 1 <= len(grants) <= MAX_GRANTS:
        raise ConsentError(f"grants must contain 1..{MAX_GRANTS} entries")
    agent_id, items, source_digest = _validate_dataset(dataset)
    checked = [validate_grant(grant) for grant in grants]
    ids = [grant["consent_id"] for grant in checked]
    if len(ids) != len(set(ids)):
        raise ConsentError("duplicate consent_id values are ambiguous")
    grant_digests = sorted(_digest(grant) for grant in checked)

    accepted = []
    for item in items:
        event = item["event"]
        if not (event["valid_from_ns"] <= timestamp
                and (event["valid_to_ns"] is None or timestamp < event["valid_to_ns"])):
            continue
        matches = []
        for grant in checked:
            active = (grant["valid_from_ns"] <= timestamp
                      and (grant["valid_to_ns"] is None or timestamp < grant["valid_to_ns"]))
            if (active and grant["agent_id"] == agent_id
                    and grant["source"] == event["source"]
                    and grant["source_revision"] == event["source_revision"]
                    and event["topic"] in grant["topics"]
                    and event["visibility"] in grant["allowed_visibility"]
                    and grant["purpose"] == requested_purpose):
                matches.append(grant["consent_id"])
        if matches:
            accepted.append({
                "seq": item["seq"], "event": item["event"],
                "event_hash": item["event_hash"], "consent_ids": sorted(matches),
            })

    result = {
        "schema": AUTHORIZED_SCHEMA,
        "agent_id": agent_id,
        "purpose": requested_purpose,
        "authorized_at_ns": timestamp,
        "source_projection_sha256": source_digest,
        "consent_grant_sha256": grant_digests,
        "items": accepted,
        "policy": "explicit-exact-source-revision-topic-visibility-window",
        "limitations": "caller-supplied-grants-not-authenticated-or-legal-consent",
    }
    return {**result, "sha256": _digest(result)}
