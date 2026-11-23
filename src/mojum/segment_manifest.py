"""Versioned, offline physical-segment manifest contract.

This module validates metadata only. It does not read/write archive files,
authenticate a remote peer, encrypt data, or establish replica consensus.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

SCHEMA = "mojum.noosphere.segment-manifest/1"
EMPTY_HEAD = "0" * 64
MAX_MANIFEST_BYTES = 16_384
_MAX_TEXT = 256
_FIELDS = frozenset({
    "schema", "agent_id", "first_seq", "last_seq", "event_count",
    "previous_segment_head", "segment_head", "source_schema",
    "content_sha256", "origin_device", "created_at_ns",
})
_HEAD_DOMAIN = b"mojum.noosphere.segment-head/1\0"


class SegmentManifestError(ValueError):
    """A segment manifest violates the versioned public metadata contract."""


def _text(value: Any, field: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > _MAX_TEXT:
        raise SegmentManifestError(f"{field} must be a nonempty string of at most {_MAX_TEXT} characters")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise SegmentManifestError(f"{field} must be valid UTF-8") from exc
    return value


def _uint(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum or value > 2**63 - 1:
        raise SegmentManifestError(f"{field} must be an integer in the supported range")
    return value


def _digest(value: Any, field: str) -> str:
    if type(value) is not str or len(value) != 64:
        raise SegmentManifestError(f"{field} must be a lowercase SHA-256 hex digest")
    if any(char not in "0123456789abcdef" for char in value):
        raise SegmentManifestError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _canonical(value: dict[str, Any]) -> bytes:
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise SegmentManifestError("manifest is not canonical UTF-8 JSON") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise SegmentManifestError("manifest exceeds maximum encoded size")
    return raw


def _head_payload(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": fields["schema"],
        "agent_id": fields["agent_id"],
        "first_seq": fields["first_seq"],
        "last_seq": fields["last_seq"],
        "event_count": fields["event_count"],
        "previous_segment_head": fields["previous_segment_head"],
        "source_schema": fields["source_schema"],
        "content_sha256": fields["content_sha256"],
    }


def _calculate_head(fields: dict[str, Any]) -> str:
    payload = _canonical(_head_payload(fields))
    return hashlib.sha256(_HEAD_DOMAIN + payload).hexdigest()


@dataclass(frozen=True, slots=True)
class SegmentManifest:
    schema: str
    agent_id: str
    first_seq: int
    last_seq: int
    event_count: int
    previous_segment_head: str
    segment_head: str
    source_schema: str
    content_sha256: str
    origin_device: str
    created_at_ns: int

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible representation."""
        return {
            "schema": self.schema,
            "agent_id": self.agent_id,
            "first_seq": self.first_seq,
            "last_seq": self.last_seq,
            "event_count": self.event_count,
            "previous_segment_head": self.previous_segment_head,
            "segment_head": self.segment_head,
            "source_schema": self.source_schema,
            "content_sha256": self.content_sha256,
            "origin_device": self.origin_device,
            "created_at_ns": self.created_at_ns,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical(self.to_dict())

    @property
    def manifest_sha256(self) -> str:
        """Consistency digest of canonical manifest bytes, not a signature."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def create_manifest(
    *,
    agent_id: str,
    first_seq: int,
    last_seq: int,
    previous_segment_head: str,
    source_schema: str,
    content_sha256: str,
    origin_device: str,
    created_at_ns: int,
) -> SegmentManifest:
    """Build a manifest and calculate its deterministic chain head."""
    fields: dict[str, Any] = {
        "schema": SCHEMA,
        "agent_id": agent_id,
        "first_seq": first_seq,
        "last_seq": last_seq,
        "event_count": (last_seq - first_seq + 1)
            if type(first_seq) is int and type(last_seq) is int else None,
        "previous_segment_head": previous_segment_head,
        "source_schema": source_schema,
        "content_sha256": content_sha256,
        "origin_device": origin_device,
        "created_at_ns": created_at_ns,
    }
    # Validate all caller fields before deriving or returning a chain head.
    validated = _validate_fields({**fields, "segment_head": EMPTY_HEAD}, check_head=False)
    fields["segment_head"] = _calculate_head(validated)
    return _validate_fields(fields)


def _validate_fields(value: dict[str, Any], *, check_head: bool = True) -> SegmentManifest:
    if type(value) is not dict or set(value) != _FIELDS:
        raise SegmentManifestError("manifest must contain exactly the required fields")
    if value["schema"] != SCHEMA:
        raise SegmentManifestError("unsupported segment manifest schema")
    agent_id = _text(value["agent_id"], "agent_id")
    source_schema = _text(value["source_schema"], "source_schema")
    origin_device = _text(value["origin_device"], "origin_device")
    first_seq = _uint(value["first_seq"], "first_seq", minimum=1)
    last_seq = _uint(value["last_seq"], "last_seq", minimum=first_seq)
    event_count = _uint(value["event_count"], "event_count", minimum=1)
    if event_count != last_seq - first_seq + 1:
        raise SegmentManifestError("event_count must equal the inclusive sequence range")
    previous = _digest(value["previous_segment_head"], "previous_segment_head")
    segment_head = _digest(value["segment_head"], "segment_head")
    content = _digest(value["content_sha256"], "content_sha256")
    created = _uint(value["created_at_ns"], "created_at_ns")
    if first_seq == 1 and previous != EMPTY_HEAD:
        raise SegmentManifestError("genesis segment must use the empty previous head")
    if first_seq > 1 and previous == EMPTY_HEAD:
        raise SegmentManifestError("non-genesis segment must link a previous head")
    normalized = {
        "schema": SCHEMA, "agent_id": agent_id,
        "first_seq": first_seq, "last_seq": last_seq,
        "event_count": event_count, "previous_segment_head": previous,
        "segment_head": segment_head, "source_schema": source_schema,
        "content_sha256": content, "origin_device": origin_device,
        "created_at_ns": created,
    }
    if check_head and _calculate_head(normalized) != segment_head:
        raise SegmentManifestError("segment_head does not match canonical manifest chain fields")
    _canonical(normalized)
    return SegmentManifest(**normalized)


def validate_manifest(value: dict[str, Any]) -> SegmentManifest:
    """Validate an in-memory exact dict; mappings/subclasses are not accepted."""
    return _validate_fields(value)


def parse_manifest(raw: bytes | bytearray | memoryview) -> SegmentManifest:
    """Parse canonical UTF-8 JSON, rejecting duplicate keys and wire ambiguity."""
    if type(raw) not in (bytes, bytearray, memoryview):
        raise SegmentManifestError("manifest wire input must be bytes-like")
    wire = bytes(raw)
    if not wire or len(wire) > MAX_MANIFEST_BYTES:
        raise SegmentManifestError("manifest wire size is outside the supported range")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise SegmentManifestError("duplicate JSON object key")
            result[key] = item
        return result

    try:
        text = wire.decode("utf-8", errors="strict")
        parsed = json.loads(text, object_pairs_hook=unique_object)
    except SegmentManifestError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise SegmentManifestError("invalid manifest JSON") from exc
    if type(parsed) is not dict:
        raise SegmentManifestError("manifest JSON root must be an object")
    manifest = validate_manifest(parsed)
    if manifest.canonical_bytes() != wire:
        raise SegmentManifestError("manifest JSON must use canonical encoding")
    return manifest


def verify_segment_bytes(manifest: SegmentManifest, segment_bytes: bytes) -> bool:
    """Compare caller-supplied segment bytes with content_sha256; performs no I/O."""
    if type(manifest) is not SegmentManifest:
        raise SegmentManifestError("manifest must be a validated SegmentManifest")
    # Dataclasses can be instantiated directly, bypassing the factory. Re-run
    # the public contract before trusting the digest field.
    validate_manifest(manifest.to_dict())
    if type(segment_bytes) is not bytes:
        raise SegmentManifestError("segment_bytes must be exact bytes")
    return hashlib.sha256(segment_bytes).hexdigest() == manifest.content_sha256
