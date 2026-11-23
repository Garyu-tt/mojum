"""Versioned, offline manifest contract for physical journal segments.

This module validates metadata only. It does not read, write, encrypt, sign,
replicate, or recover segment files.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

SCHEMA = "mojum.physical-segment-manifest/1"
ZERO_HEAD = "0" * 64
MAX_IDENTIFIER = 128
MAX_SOURCE_SCHEMA = 160
MAX_TIMESTAMP_NS = (1 << 63) - 1
_HEX_256 = re.compile(r"[0-9a-f]{64}\\Z")
_IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}\\Z")
_SOURCE_SCHEMA = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,159}\\Z")
_FIELDS = frozenset({
    "schema", "agent_id", "first_seq", "last_seq", "event_count",
    "previous_segment_head", "segment_head", "source_schema",
    "content_sha256", "origin_device_id", "created_at_ns",
})


class SegmentManifestError(ValueError):
    """Raised when a segment manifest is malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class SegmentManifest:
    schema: str
    agent_id: str
    first_seq: int
    last_seq: int
    event_count: int
    previous_segment_head: str | None
    segment_head: str
    source_schema: str
    content_sha256: str
    origin_device_id: str
    created_at_ns: int

    def as_dict(self) -> dict[str, Any]:
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
            "origin_device_id": self.origin_device_id,
            "created_at_ns": self.created_at_ns,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.as_dict())


def canonical_json(value: Mapping[str, Any]) -> str:
    """Serialize validated primitive metadata deterministically."""
    try:
        return json.dumps(
            dict(value), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise SegmentManifestError("manifest is not canonical JSON data") from exc


def _identifier(value: Any, field: str, maximum: int = MAX_IDENTIFIER, pattern: re.Pattern[str] = _IDENTIFIER) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise SegmentManifestError(f"{field} must be a non-empty bounded string")
    if not pattern.fullmatch(value):
        raise SegmentManifestError(f"{field} contains unsupported characters")
    return value


def _digest(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or not _HEX_256.fullmatch(value):
        raise SegmentManifestError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _native_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SegmentManifestError(f"{field} must be an integer in [{minimum}, {maximum}]")
    return value


def _head_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    return {key: data[key] for key in sorted(_FIELDS - {"segment_head"})}


def calculate_segment_head(data: Mapping[str, Any]) -> str:
    """Calculate the unkeyed integrity-chain digest for a manifest body.

    This is a consistency hash, NOT a signature or authentication proof.
    """
    payload = _head_payload(data)
    encoded = canonical_json(payload).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_manifest(
    *,
    agent_id: str,
    first_seq: int,
    last_seq: int,
    previous_segment_head: str | None,
    source_schema: str,
    content_sha256: str,
    origin_device_id: str,
    created_at_ns: int,
) -> SegmentManifest:
    """Build a manifest and derive its event count and chain head."""
    raw = {
        "schema": SCHEMA,
        "agent_id": agent_id,
        "first_seq": first_seq,
        "last_seq": last_seq,
        "event_count": last_seq - first_seq + 1 if type(first_seq) is int and type(last_seq) is int else None,
        "previous_segment_head": previous_segment_head,
        "segment_head": ZERO_HEAD,
        "source_schema": source_schema,
        "content_sha256": content_sha256,
        "origin_device_id": origin_device_id,
        "created_at_ns": created_at_ns,
    }
    # Validate every field before deriving the head.
    _validate_fields(raw, check_head=False)
    raw["segment_head"] = calculate_segment_head(raw)
    return _validate_fields(raw, check_head=True)


def _validate_fields(data: Mapping[str, Any], *, check_head: bool) -> SegmentManifest:
    if type(data) is not dict:
        raise SegmentManifestError("manifest must be an exact built-in dict")
    keys = frozenset(data)
    if keys != _FIELDS:
        missing = sorted(_FIELDS - keys)
        extra = sorted(keys - _FIELDS)
        raise SegmentManifestError(f"manifest fields mismatch (missing={missing}, extra={extra})")
    if data["schema"] != SCHEMA or type(data["schema"]) is not str:
        raise SegmentManifestError("unsupported manifest schema")
    agent_id = _identifier(data["agent_id"], "agent_id")
    first = _native_int(data["first_seq"], "first_seq", 0, MAX_TIMESTAMP_NS)
    last = _native_int(data["last_seq"], "last_seq", first, MAX_TIMESTAMP_NS)
    count = _native_int(data["event_count"], "event_count", 1, MAX_TIMESTAMP_NS)
    if last - first + 1 != count:
        raise SegmentManifestError("event_count must equal the inclusive sequence interval")
    previous = _digest(data["previous_segment_head"], "previous_segment_head", nullable=True)
    source_schema = _identifier(data["source_schema"], "source_schema", MAX_SOURCE_SCHEMA, _SOURCE_SCHEMA)
    content_hash = _digest(data["content_sha256"], "content_sha256")
    origin = _identifier(data["origin_device_id"], "origin_device_id")
    created = _native_int(data["created_at_ns"], "created_at_ns", 0, MAX_TIMESTAMP_NS)
    head = _digest(data["segment_head"], "segment_head")
    if check_head and head != calculate_segment_head(data):
        raise SegmentManifestError("segment_head does not match canonical manifest metadata")
    return SegmentManifest(
        SCHEMA, agent_id, first, last, count, previous, head,
        source_schema, content_hash, origin, created,
    )


def validate_manifest(data: Mapping[str, Any]) -> SegmentManifest:
    """Validate an exact v1 manifest and return a detached immutable value."""
    return _validate_fields(data, check_head=True)


def parse_manifest_json(raw: str | bytes) -> SegmentManifest:
    """Parse bounded JSON while rejecting duplicate keys and non-standard constants."""
    if type(raw) not in (str, bytes):
        raise SegmentManifestError("wire input must be str or bytes")
    try:
        encoded = raw.encode("utf-8") if type(raw) is str else raw
        if len(encoded) > 16_384:
            raise SegmentManifestError("manifest JSON exceeds 16384 bytes")
        text = encoded.decode("utf-8", errors="strict")
        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise SegmentManifestError("duplicate JSON object key")
                result[key] = value
            return result
        parsed = json.loads(
            text, object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                SegmentManifestError("non-finite JSON numbers are forbidden")
            ),
        )
    except SegmentManifestError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SegmentManifestError("invalid manifest JSON") from exc
    return validate_manifest(parsed)
