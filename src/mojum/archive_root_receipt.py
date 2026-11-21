"""R0.19 explicit HMAC owner receipts for independently verified PRIVATE archives.

The owner must physically keep the secret and the pinned receipt digest
outside the archive's failure domain. A HMAC by itself is integrity, not
encryption, public-key attestation, temporal freshness or anti-rollback.

No background synchronization, automatic copying, external network access,
or authority to overwrite a prior receipt is implemented here.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat

from .verified_snapshot import canonical, read_key, _open_regular, SnapshotError
from .segment_archive import ArchiveError, private_directory, publish_new
from .live_tail_capsule import verify_point_in_time

SCHEMA = "mojum.noosphere.owner-archive-receipt/1"
_HMAC_DOMAIN = b"mojum.noosphere.owner-archive-receipt/1\x00"
MAX_RECEIPT_BYTES = 4096
_RECEIPT_FIELDS = {"schema", "statement", "hmac_sha256"}
_STATEMENT_FIELDS = {
    "schema", "agent_id", "source_events", "source_head",
    "segment_size", "sealed_segments", "sealed_events",
    "tail_events", "sealed_head", "contains_private",
}
_STATEMENT_SCHEMA = "mojum.noosphere.owner-archive-receipt-statement/1"
_HEX = frozenset("0123456789abcdef")


class ReceiptError(ValueError):
    """The archive/root receipt is unauthenticated, unsafe or inconsistent."""


def _digest(value):
    return type(value) is str and len(value) == 64 and all(x in _HEX for x in value)


def _canonical_bytes(value):
    try:
        result = canonical(value)
    except SnapshotError as exc:
        raise ReceiptError("noncanonical receipt JSON") from exc
    if len(result) > MAX_RECEIPT_BYTES:
        raise ReceiptError("receipt exceeds bounded 4096-byte budget")
    return result


def _check_statement(s):
    if (type(s) is not dict or set(s) != _STATEMENT_FIELDS
            or s["schema"] != _STATEMENT_SCHEMA
            or type(s["agent_id"]) is not str
            or not 1 <= len(s["agent_id"]) <= 256
            or type(s["source_events"]) is not int
            or not 1 <= s["source_events"] <= 262144
            or not _digest(s["source_head"])
            or not _digest(s["sealed_head"])
            or type(s["segment_size"]) is not int
            or not 64 <= s["segment_size"] <= 8192
            or type(s["sealed_segments"]) is not int
            or s["sealed_segments"] < 0
            or type(s["sealed_events"]) is not int
            or s["sealed_events"] != s["sealed_segments"]*s["segment_size"]
            or type(s["tail_events"]) is not int
            or not 1 <= s["tail_events"] < s["segment_size"]
            or s["source_events"] != s["sealed_events"]+s["tail_events"]
            or type(s["contains_private"]) is not bool):
        raise ReceiptError("invalid versioned owner archive statement")
    return s


def _statement_for_archive(archive_dir, *, source_events,
                           external_expected_source_head):
    result = verify_point_in_time(
        archive_dir, source_events=source_events,
        external_expected_source_head=external_expected_source_head,
    )
    return _check_statement({
        "schema": _STATEMENT_SCHEMA,
        "agent_id": result["agent_id"],
        "source_events": result["archived_events"],
        "source_head": result["source_head"],
        "segment_size": result["segment_size"],
        "sealed_segments": result["sealed_segments"],
        "sealed_events": result["sealed_events"],
        "tail_events": result["tail_events"],
        "sealed_head": result["sealed_head"],
        "contains_private": result["contains_private"],
    })


def sign_owner_receipt(archive_dir, receipt_file, key_file, *,
                       source_events, external_expected_source_head):
    """Sign one exact pinned archive state to a NEW external receipt file.

    No overwritten receipt, archived payload, or implicit network writes.
    The out-of-band hash returned must itself be pinned independently.
    """
    archive = private_directory(archive_dir)
    dest = Path(receipt_file)
    parent = private_directory(dest.parent)
    if (dest.is_symlink() or dest.exists() or dest.is_dir()):
        raise ReceiptError("receipt destination must be a new absent file")
    try:
        if (parent.resolve() == archive.resolve()
                or parent.resolve().is_relative_to(archive.resolve())):
            raise ReceiptError("receipt may not be placed inside archive tree")
    except (OSError, RuntimeError) as exc:
        raise ReceiptError("receipt location cannot be resolved") from exc
    secret_path = Path(key_file).resolve()
    if secret_path.is_relative_to(archive.resolve()):
        raise ReceiptError("owner signing key cannot be stored in archive tree")
    key = read_key(key_file)
    statement = _statement_for_archive(
        archive, source_events=source_events,
        external_expected_source_head=external_expected_source_head,
    )
    mac = hmac.new(key, _HMAC_DOMAIN + _canonical_bytes(statement), hashlib.sha256).hexdigest()
    envelope = {
        "schema": SCHEMA, "statement": statement, "hmac_sha256": mac,
    }
    raw = _canonical_bytes(envelope)
    try:
        publish_new(dest, [raw])
    except ArchiveError as exc:
        raise ReceiptError("receipt publication failed or already exists") from exc
    return {
        "schema": SCHEMA, "source_events": source_events,
        "source_head": external_expected_source_head,
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "authenticated": "owner-hmac-not-independent-temporal-proof",
    }


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ReceiptError("duplicate key in signed receipt")
        result[key] = value
    return result


def verify_owner_receipt(receipt_file, key_file, *,
                         independently_pinned_receipt_sha256):
    """Only an independently recorded receipt digest rejects old signed roots."""
    if not _digest(independently_pinned_receipt_sha256):
        raise ReceiptError("independently pinned receipt SHA256 required")
    key = read_key(key_file)
    try:
        raw = _open_regular(receipt_file, secret=False)
    except SnapshotError as exc:
        raise ReceiptError("unsafe or missing receipt path") from exc
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ReceiptError("receipt is oversized")
    if not hmac.compare_digest(
            hashlib.sha256(raw).hexdigest(), independently_pinned_receipt_sha256):
        raise ReceiptError("receipt mismatches independent expected digest")
    try:
        envelope = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ReceiptError("invalid signed receipt JSON") from exc
    if (type(envelope) is not dict or set(envelope) != _RECEIPT_FIELDS
            or envelope["schema"] != SCHEMA
            or not _digest(envelope["hmac_sha256"])):
        raise ReceiptError("invalid versioned signed receipt envelope")
    statement = _check_statement(envelope["statement"])
    if _canonical_bytes(envelope) != raw:
        raise ReceiptError("noncanonical signed receipt envelope")
    expected = hmac.new(
        key, _HMAC_DOMAIN + _canonical_bytes(statement), hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, envelope["hmac_sha256"]):
        raise ReceiptError("HMAC owner signature mismatch")
    return statement


def verify_two_archive_copies(archive_a, archive_b, receipt_file, key_file, *,
                              independently_pinned_receipt_sha256):
    """Verify two explicitly supplied local archive copies against ONE root.

    This only checks byte-level history equivalence and authorization.
    It DOES NOT certify independent physical failure domains or remote
    durability. No copying or alteration is performed.
    """
    root_a = private_directory(archive_a)
    root_b = private_directory(archive_b)
    if root_a.resolve() == root_b.resolve():
        raise ReceiptError("two archive paths must be distinct")
    statement = verify_owner_receipt(
        receipt_file, key_file,
        independently_pinned_receipt_sha256=independently_pinned_receipt_sha256,
    )
    for path in (root_a, root_b):
        verified = _statement_for_archive(
            path, source_events=statement["source_events"],
            external_expected_source_head=statement["source_head"],
        )
        if not hmac.compare_digest(
                _canonical_bytes(verified), _canonical_bytes(statement)):
            raise ReceiptError("archive copy differs from owner receipt")
    return {
        "schema": SCHEMA, "source_events": statement["source_events"],
        "source_head": statement["source_head"], "copies_verified": 2,
        "trust": "same-owner-authenticated-content-not-physical-redundancy",
    }
