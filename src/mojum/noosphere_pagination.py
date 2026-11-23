"""Bounded PUBLIC-only keyset pagination over a pinned Noosphere snapshot."""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
from typing import Any

PAGE_SCHEMA = "mojum.noosphere.page/1"
CURSOR_SCHEMA = "mojum.noosphere.page-cursor/1"
EMPTY_HEAD = "0" * 64
MAX_PAGE_SIZE = 1000
BATCH_SIZE = 128
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
CURSOR_KEYS = frozenset({"schema", "agent_id", "snapshot_count", "snapshot_head",
                         "after_seq", "page_size", "filters", "cursor_sha256"})
FILTER_KEYS = frozenset({"topic", "source", "as_of_recorded_ns", "valid_at_ns"})


class PaginationError(ValueError):
    """Invalid paging request, cursor, or pinned journal snapshot."""


def _canonical(value: Any) -> str:
    try:
        wire = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
        wire.encode("utf-8")
        return wire
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        raise PaginationError("value is not canonical UTF-8 JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _int(value: Any, name: str, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise PaginationError(f"{name} is outside its integer bounds")
    return value


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > 256:
        raise PaginationError(f"{name} must be a nonempty native string <= 256 chars")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PaginationError(f"{name} must be valid UTF-8") from exc
    return value


def _filters(topic, source, as_of_recorded_ns, valid_at_ns):
    if topic is not None:
        _text(topic, "topic")
    if source is not None:
        _text(source, "source")
    if as_of_recorded_ns is not None:
        _int(as_of_recorded_ns, "as_of_recorded_ns")
    if valid_at_ns is not None:
        _int(valid_at_ns, "valid_at_ns")
    return {"topic": topic, "source": source,
            "as_of_recorded_ns": as_of_recorded_ns, "valid_at_ns": valid_at_ns}


def _parse_event(raw):
    if type(raw) is not str:
        raise PaginationError("journal wire value must be text")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PaginationError("duplicate key in journal event")
            result[key] = value
        return result

    try:
        event = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(
                               PaginationError("non-finite journal number")))
    except PaginationError:
        raise
    except (TypeError, ValueError, RecursionError) as exc:
        raise PaginationError("invalid journal event JSON") from exc
    if type(event) is not dict:
        raise PaginationError("journal event must be an object")
    return event


def _store_parts(store):
    db = getattr(store, "_db", None)
    owner = getattr(store, "agent_id", None)
    if type(db) is not sqlite3.Connection or type(owner) is not str:
        raise PaginationError("expected an open LocalNoosphere SQLite store")
    if getattr(store, "read_only", None) is not True:
        raise PaginationError("pagination requires read_only=True")
    verify = getattr(store, "verify_chain", None)
    if not callable(verify) or db.in_transaction:
        raise PaginationError("store verifier missing or caller transaction active")
    return db, owner, verify


def _cursor_body(cursor):
    return {key: value for key, value in cursor.items() if key != "cursor_sha256"}


def _make_cursor(owner, count, head, after_seq, page_size, filters):
    body = {"schema": CURSOR_SCHEMA, "agent_id": owner,
            "snapshot_count": count, "snapshot_head": head,
            "after_seq": after_seq, "page_size": page_size,
            "filters": dict(filters)}
    return {**body, "cursor_sha256": _digest(body)}


def _read_cursor(cursor, owner, requested_size, requested_filters):
    if type(cursor) is not dict or len(cursor) != len(CURSOR_KEYS):
        raise PaginationError("cursor must have the exact native object schema")
    if any(type(key) is not str for key in cursor) or set(cursor) != CURSOR_KEYS:
        raise PaginationError("cursor fields are invalid")
    if type(cursor["schema"]) is not str or cursor["schema"] != CURSOR_SCHEMA:
        raise PaginationError("unsupported cursor schema")
    if type(cursor["agent_id"]) is not str or cursor["agent_id"] != owner:
        raise PaginationError("cursor belongs to another agent")
    count = _int(cursor["snapshot_count"], "snapshot_count")
    after = _int(cursor["after_seq"], "after_seq")
    size = _int(cursor["page_size"], "page_size", 1, MAX_PAGE_SIZE)
    head = cursor["snapshot_head"]
    if type(head) is not str or not HASH_RE.fullmatch(head):
        raise PaginationError("invalid pinned journal head")
    saved = cursor["filters"]
    if type(saved) is not dict or len(saved) != len(FILTER_KEYS):
        raise PaginationError("cursor filters have invalid shape")
    if any(type(key) is not str for key in saved) or set(saved) != FILTER_KEYS:
        raise PaginationError("cursor filter keys are invalid")
    checked = _filters(saved["topic"], saved["source"],
                       saved["as_of_recorded_ns"], saved["valid_at_ns"])
    if checked != saved or after > count:
        raise PaginationError("cursor state is inconsistent")
    if requested_size is not None and requested_size != size:
        raise PaginationError("page size changed during paging")
    if requested_filters is not None and requested_filters != checked:
        raise PaginationError("filters changed during paging")
    checksum = cursor["cursor_sha256"]
    if type(checksum) is not str or not HASH_RE.fullmatch(checksum):
        raise PaginationError("invalid cursor checksum")
    if _digest(_cursor_body(cursor)) != checksum:
        raise PaginationError("cursor checksum mismatch")
    return count, head, after, size, checked


def page_public_dataset(store, *, cursor=None, topic=None, source=None,
                        as_of_recorded_ns=None, valid_at_ns=None,
                        page_size=None, expected_snapshot_head=None):
    """Return a PUBLIC-only page pinned to the first call's verified journal prefix.

    Use LocalNoosphere(path, agent_id, read_only=True). Cursor SHA-256 is a
    consistency digest, not authentication. Appends after page one are excluded.
    """
    db, owner, verify = _store_parts(store)
    requested_filters = None
    if cursor is None:
        filters = _filters(topic, source, as_of_recorded_ns, valid_at_ns)
        size = 100 if page_size is None else _int(page_size, "page_size", 1, MAX_PAGE_SIZE)
        if expected_snapshot_head is not None and (
            type(expected_snapshot_head) is not str or not HASH_RE.fullmatch(expected_snapshot_head)
        ):
            raise PaginationError("expected_snapshot_head must be lowercase SHA-256")
    else:
        if expected_snapshot_head is not None:
            raise PaginationError("external anchor is accepted only on page one")
        if any(value is not None for value in (topic, source, as_of_recorded_ns, valid_at_ns)):
            requested_filters = _filters(topic, source, as_of_recorded_ns, valid_at_ns)
        count, head, after, size, filters = _read_cursor(
            cursor, owner, page_size, requested_filters)

    try:
        db.execute("BEGIN")
        verified = verify(strict_wire=True)
        current_count = _int(verified["count"], "verified_count")
        current_head = verified["head"]
        if type(current_head) is not str or not HASH_RE.fullmatch(current_head):
            raise PaginationError("canonical verifier returned invalid head")
        if cursor is None:
            count, head, after = current_count, current_head, 0
            if expected_snapshot_head is not None and head != expected_snapshot_head:
                raise PaginationError("journal head differs from external anchor")
        else:
            if current_count < count:
                raise PaginationError("journal is shorter than pinned snapshot")
            if count == 0:
                pinned = EMPTY_HEAD
            else:
                row = db.execute("SELECT event_hash FROM events WHERE seq = ?", (count,)).fetchone()
                pinned = row["event_hash"] if row is not None else None
            if pinned != head:
                raise PaginationError("pinned journal prefix has changed")

        where = ["agent_id = ?", "visibility = 'PUBLIC'", "seq > ?", "seq <= ?"]
        args = [owner, after, count]
        if filters["topic"] is not None:
            where.append("topic = ?")
            args.append(filters["topic"])
        if filters["as_of_recorded_ns"] is not None:
            where.append("recorded_at_ns <= ?")
            args.append(filters["as_of_recorded_ns"])
        if filters["valid_at_ns"] is not None:
            where.append("valid_from_ns <= ? AND (valid_to_ns IS NULL OR valid_to_ns > ?)")
            args.extend((filters["valid_at_ns"], filters["valid_at_ns"]))
        sql = ("SELECT seq, wire_json, event_hash FROM events WHERE "
               + " AND ".join(where) + " ORDER BY seq")
        rows = db.execute(sql, args)
        items, has_more, last_seq = [], False, after
        try:
            while True:
                batch = rows.fetchmany(BATCH_SIZE)
                if not batch:
                    break
                for row in batch:
                    event = _parse_event(row["wire_json"])
                    if filters["source"] is not None and event.get("source") != filters["source"]:
                        continue
                    if len(items) == size:
                        has_more = True
                        break
                    items.append({"seq": row["seq"], "event": event,
                                  "event_hash": row["event_hash"]})
                    last_seq = row["seq"]
                if has_more:
                    break
        finally:
            rows.close()

        next_cursor = (_make_cursor(owner, count, head, last_seq, size, filters)
                       if has_more else None)
        page = {"schema": PAGE_SCHEMA, "agent_id": owner,
                "snapshot": {"count": count, "head": head},
                "filters": {**filters, "visibility": "PUBLIC"},
                "page_size": size, "items": items, "next_cursor": next_cursor}
        result = {"page": page, "sha256": _digest(page)}
        db.execute("COMMIT")
        return result
    except PaginationError:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    except (sqlite3.Error, KeyError, TypeError, ValueError, RecursionError) as exc:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise PaginationError("cannot produce verified Noosphere page") from exc
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
