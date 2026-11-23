"""Bounded offline verifier for metadata-only QA supervisor audit chains.

Digests provide consistency checking, not authentication or authorization.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

SCHEMA = "mojum.qa-supervisor.audit-chain/1"
GENESIS = "0" * 64
MAX_RECORDS = 4096
MAX_TASK_KEY = 96
MAX_REASON = 64
KINDS = frozenset({"enqueued", "claimed", "heartbeat", "retried", "completed", "blocked", "failed", "unblocked"})
STATES = frozenset({"queued", "claimed", "retry_wait", "completed", "blocked", "dead"})
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = frozenset({"seq", "kind", "task_key", "state", "reason", "at_ms", "previous_digest", "digest"})


class AuditChainError(ValueError):
    """Raised when an audit chain is malformed or inconsistent."""


def _native_int(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise AuditChainError(f"{name} must be a native integer >= {minimum}")
    return value


def _canonical(record: Mapping[str, Any]) -> bytes:
    unsigned = {key: record[key] for key in (
        "seq", "kind", "task_key", "state", "reason", "at_ms", "previous_digest"
    )}
    try:
        return json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                          allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise AuditChainError("record is not canonical JSON data") from exc


def record_digest(record: Mapping[str, Any]) -> str:
    """Return the SHA-256 consistency digest for a valid-shaped unsigned record."""
    return hashlib.sha256(_canonical(record)).hexdigest()


def verify_audit_chain(records: Any, *, max_records: int = MAX_RECORDS) -> dict[str, Any]:
    """Verify a complete ordered chain and return metadata only.

    Input is a list/tuple of exact-schema PUBLIC_SYNTHETIC records. Sequence starts
    at 1, timestamps are nondecreasing, and each record links to its predecessor.
    """
    if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
        raise AuditChainError("max_records is outside the supported bound")
    if type(records) not in (list, tuple):
        raise AuditChainError("records must be a list or tuple")
    if len(records) > max_records:
        raise AuditChainError("record count exceeds the configured bound")

    previous = GENESIS
    previous_at = -1
    for index, item in enumerate(records, 1):
        if not isinstance(item, Mapping):
            raise AuditChainError(f"record {index} must be a mapping")
        if set(item.keys()) != _FIELDS:
            raise AuditChainError(f"record {index} has missing or unknown fields")
        seq = _native_int(item["seq"], "seq", 1)
        at_ms = _native_int(item["at_ms"], "at_ms", 0)
        if seq != index:
            raise AuditChainError("sequence must be contiguous and start at 1")
        if at_ms < previous_at:
            raise AuditChainError("timestamps must be nondecreasing")
        if item["kind"] not in KINDS or type(item["kind"]) is not str:
            raise AuditChainError("unsupported event kind")
        if item["state"] not in STATES or type(item["state"]) is not str:
            raise AuditChainError("unsupported task state")
        if type(item["task_key"]) is not str or not _TOKEN.fullmatch(item["task_key"]):
            raise AuditChainError("task_key is malformed")
        reason = item["reason"]
        if reason is not None and (type(reason) is not str or not _TOKEN.fullmatch(reason[:MAX_REASON]) or len(reason) > MAX_REASON):
            raise AuditChainError("reason must be a bounded token or null")
        if type(item["previous_digest"]) is not str or not _HEX.fullmatch(item["previous_digest"]):
            raise AuditChainError("previous_digest must be lowercase SHA-256 hex")
        if item["previous_digest"] != previous:
            raise AuditChainError("previous digest does not match chain head")
        if type(item["digest"]) is not str or not _HEX.fullmatch(item["digest"]):
            raise AuditChainError("digest must be lowercase SHA-256 hex")
        expected = hashlib.sha256(_canonical(item)).hexdigest()
        if item["digest"] != expected:
            raise AuditChainError("record digest mismatch")
        previous = expected
        previous_at = at_ms

    return {
        "schema": SCHEMA,
        "verified": True,
        "record_count": len(records),
        "first_seq": 1 if records else None,
        "last_seq": len(records) if records else None,
        "head_digest": previous,
    }
