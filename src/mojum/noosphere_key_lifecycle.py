"""Offline metadata-only lifecycle checks for caller-managed export keys.

This module never receives key bytes or performs cryptography.
"""
from __future__ import annotations

import hashlib
import json
import re

SCHEMA = "mojum.noosphere-key-lifecycle/1"
_PURPOSE = "noosphere-export"
_ALGORITHM = "AES-256-GCM"
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FIELDS = {"key_id", "purpose", "algorithm", "created_at", "activated_at",
           "retired_at", "revoked_at", "material_available"}


class KeyLifecycleError(ValueError):
    """Raised when key lifecycle metadata is malformed or unsafe."""


def _timestamp(value, field, *, optional=False):
    if optional and value is None:
        return
    if type(value) is not int or value < 0:
        raise KeyLifecycleError(f"{field} must be a non-negative integer timestamp")


def validate_keyring(records, *, now):
    """Validate and detach a bounded keyring; return canonical metadata + digest."""
    _timestamp(now, "now")
    if type(records) is not list or not 1 <= len(records) <= 128:
        raise KeyLifecycleError("records must be a list containing 1..128 entries")
    normalized = []
    seen = set()
    for index, record in enumerate(records):
        if type(record) is not dict or set(record) != _FIELDS:
            raise KeyLifecycleError(f"record {index} has an invalid shape")
        key_id = record["key_id"]
        if type(key_id) is not str or not _KEY_ID.fullmatch(key_id):
            raise KeyLifecycleError(f"record {index} has an invalid key_id")
        if key_id in seen:
            raise KeyLifecycleError("duplicate key_id")
        seen.add(key_id)
        if record["purpose"] != _PURPOSE or type(record["purpose"]) is not str:
            raise KeyLifecycleError(f"{key_id}: unsupported purpose")
        if record["algorithm"] != _ALGORITHM or type(record["algorithm"]) is not str:
            raise KeyLifecycleError(f"{key_id}: unsupported algorithm")
        for field in ("created_at", "activated_at"):
            _timestamp(record[field], field)
        for field in ("retired_at", "revoked_at"):
            _timestamp(record[field], field, optional=True)
        if type(record["material_available"]) is not bool:
            raise KeyLifecycleError(f"{key_id}: material_available must be boolean")
        created, activated = record["created_at"], record["activated_at"]
        retired, revoked = record["retired_at"], record["revoked_at"]
        if activated < created or activated > now:
            raise KeyLifecycleError(f"{key_id}: invalid activation interval")
        if retired is not None and (retired < activated or retired > now):
            raise KeyLifecycleError(f"{key_id}: invalid retirement interval")
        if revoked is not None and (revoked < activated or revoked > now):
            raise KeyLifecycleError(f"{key_id}: invalid revocation interval")
        normalized.append({field: record[field] for field in sorted(_FIELDS)})
    normalized.sort(key=lambda item: item["key_id"])
    encoded = json.dumps({"schema": SCHEMA, "keys": normalized},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return {"schema": SCHEMA, "keys": tuple(
        tuple(sorted(item.items())) for item in normalized),
        "digest": hashlib.sha256(encoded).hexdigest()}


def admit_key_operation(key_record, *, operation, at):
    """Return an advisory admission result for encrypt/decrypt at logical time."""
    if type(key_record) is not dict or set(key_record) != _FIELDS:
        raise KeyLifecycleError("invalid key record")
    _timestamp(at, "at")
    if operation not in ("encrypt", "decrypt") or type(operation) is not str:
        raise KeyLifecycleError("operation must be encrypt or decrypt")
    # Reuse the complete schema and chronology validation for a single record.
    validate_keyring([key_record], now=max(at, key_record.get("revoked_at") or 0,
                                          key_record.get("retired_at") or 0,
                                          key_record.get("activated_at") or 0))
    if at < key_record["activated_at"]:
        return {"allowed": False, "reason": "not_yet_active"}
    if key_record["revoked_at"] is not None and at >= key_record["revoked_at"]:
        return {"allowed": False, "reason": "revoked"}
    if operation == "encrypt" and key_record["retired_at"] is not None and at >= key_record["retired_at"]:
        return {"allowed": False, "reason": "retired_for_encryption"}
    if not key_record["material_available"]:
        return {"allowed": False, "reason": "key_material_unavailable"}
    return {"allowed": True, "reason": "active" if operation == "encrypt" else "decryptable"}
