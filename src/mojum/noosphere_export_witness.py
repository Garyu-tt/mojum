"""Reproducible, PUBLIC-only Noosphere dataset export receipts.

The receipt binds a deterministic dataset projection to one verified journal
snapshot. It is a provenance/reproducibility witness, NOT authentication,
consent, encryption, or publication authorization.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from .noosphere import DATASET_SCHEMA, EMPTY_HEAD, LocalNoosphere, NoosphereError

EXPORT_WITNESS_SCHEMA = "mojum.noosphere.public-export-witness/1"
_ALLOWED_FILTERS = frozenset(("topic", "source", "as_of_recorded_ns", "valid_at_ns"))
_HEX = frozenset("0123456789abcdef")
_MAX_LIMIT = 10_000
_MAX_INT = 2**63 - 1


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise NoosphereError("cannot encode export witness") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _validate_head(value: object) -> str:
    if (type(value) is not str or len(value) != 64
            or any(char not in _HEX for char in value)):
        raise NoosphereError("expected_head must be a lowercase SHA-256 hex digest")
    return value


def _validate_filters(filters: object) -> dict:
    if filters is None:
        return {}
    if type(filters) is not dict:
        raise NoosphereError("filters must be a native dictionary")
    keys = list(filters)
    if any(type(key) is not str for key in keys):
        raise NoosphereError("filter keys must be native strings")
    if any(key not in _ALLOWED_FILTERS for key in keys):
        raise NoosphereError("unsupported export filter")
    result = {}
    for key in keys:
        value = filters[key]
        if key in ("topic", "source"):
            if (type(value) is not str or not value.strip() or len(value) > 256):
                raise NoosphereError(f"{key} must be a nonempty string of <= 256 characters")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise NoosphereError(f"{key} must be valid UTF-8") from exc
        elif type(value) is not int or not 0 <= value <= _MAX_INT:
            raise NoosphereError(f"{key} must be a nonnegative native integer")
        result[key] = value
    return result


def create_public_export_witness(
    store: LocalNoosphere,
    *,
    filters: dict | None = None,
    limit: int = 1000,
    expected_head: str | None = None,
) -> dict:
    """Create a stable receipt for one verified PUBLIC-only dataset snapshot.

    An explicit SQLite read transaction pins verification and projection to
    the same database snapshot, so concurrent appends cannot produce a receipt
    whose source head and projected rows refer to different journal states.
    The caller may provide an independently retained SHA-256 head; without it,
    chain verification detects internal inconsistency but not malicious
    rewriting by a process that controls the database.
    """
    if type(store) is not LocalNoosphere:
        raise NoosphereError("store must be an exact LocalNoosphere instance")
    if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
        raise NoosphereError("limit must be an integer between 1 and 10000")
    checked_filters = _validate_filters(filters)
    anchor = None if expected_head is None else _validate_head(expected_head)
    db = store._db
    if db.in_transaction:
        raise NoosphereError("export witness requires an idle journal connection")

    transaction_open = False
    try:
        db.execute("BEGIN")
        transaction_open = True

        before = store.verify_chain(external_expected_head=anchor)
        projection_result = store.dataset(
            **checked_filters, include_nonpublic=False, limit=limit,
        )
        if type(projection_result) is not dict or set(projection_result) != {"projection", "sha256"}:
            raise NoosphereError("dataset returned an invalid projection envelope")
        projection = projection_result["projection"]
        if type(projection) is not dict or set(projection) != {
            "schema", "agent_id", "filters", "items"
        }:
            raise NoosphereError("dataset returned an invalid projection")
        if projection["schema"] != DATASET_SCHEMA or projection["agent_id"] != store.agent_id:
            raise NoosphereError("dataset schema or owner mismatch")
        expected_filters = {
            "topic": checked_filters.get("topic"),
            "source": checked_filters.get("source"),
            "as_of_recorded_ns": checked_filters.get("as_of_recorded_ns"),
            "valid_at_ns": checked_filters.get("valid_at_ns"),
            "include_nonpublic": False,
            "limit": limit,
        }
        if projection["filters"] != expected_filters:
            raise NoosphereError("dataset filters differ from the requested PUBLIC-only scope")
        items = projection["items"]
        if type(items) is not list or len(items) > limit:
            raise NoosphereError("dataset item list is invalid or exceeds the requested limit")
        for item in items:
            if (type(item) is not dict or set(item) != {"seq", "event", "event_hash"}
                    or type(item["event"]) is not dict
                    or item["event"].get("visibility") != "PUBLIC"):
                raise NoosphereError("dataset contains a malformed or non-PUBLIC item")
        projection_sha = _digest(projection)
        if projection_result["sha256"] != projection_sha:
            raise NoosphereError("dataset projection digest mismatch")

        after = store.verify_chain(external_expected_head=anchor)
        if before["head"] != after["head"] or before["count"] != after["count"]:
            raise NoosphereError("journal changed while creating export witness")

        receipt = {
            "schema": EXPORT_WITNESS_SCHEMA,
            "agent_id": store.agent_id,
            "source_schema": before["schema"],
            "source_count": before["count"],
            "source_head": before["head"],
            "external_anchor_verified": anchor is not None,
            "visibility": "PUBLIC",
            "filters": expected_filters,
            "exported_count": len(items),
            "projection_sha256": projection_sha,
        }
        result = {"schema": EXPORT_WITNESS_SCHEMA, "receipt": receipt,
                  "sha256": _digest(receipt)}
        db.execute("COMMIT")
        transaction_open = False
        return result
    except NoosphereError:
        raise
    except sqlite3.Error as exc:
        raise NoosphereError("cannot create a consistent export witness") from exc
    finally:
        if transaction_open and db.in_transaction:
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
