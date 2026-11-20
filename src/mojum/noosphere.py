"""Mojum Noosphere R0.1: local, append-only SQLite event memory.

This is single-owner local storage, not a distributed encrypted memory service.
A hash chain detects accidental or untrusted modification when its own head
is trusted independently; a local attacker with filesystem access can rewrite it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
from pathlib import Path

EVENT_SCHEMA = "mojum.noosphere.event/1"
STORE_SCHEMA = "mojum.noosphere.sqlite/1"
DATASET_SCHEMA = "mojum.noosphere.dataset/1"
_ALLOWED_VISIBILITY = frozenset(("PUBLIC", "PRIVATE", "RESTRICTED"))
_ZERO = "0" * 64
_MAX_PAYLOAD_BYTES = 128_000


class NoosphereError(ValueError):
    """Invalid command, invalid event, or invalid local event store."""


def _text(value, field, max_length=256):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise NoosphereError(f"{field} requires a nonempty string of <= {max_length} characters")
    return value


def _integer(value, field, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise NoosphereError(f"{field} must be integer between {minimum} and {maximum}")
    return value


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NoosphereError("event must contain canonical JSON") from exc


def _sha(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _validate_event(event, owner):
    mandatory = {
        "schema", "event_id", "agent_id", "topic", "observed_at_ns",
        "recorded_at_ns", "valid_from_ns", "valid_to_ns", "visibility",
        "source", "source_revision", "payload",
    }
    if not isinstance(event, dict) or set(event) != mandatory:
        raise NoosphereError("event must contain exactly required contract fields")
    if event["schema"] != EVENT_SCHEMA:
        raise NoosphereError("unsupported event schema")
    for name in ("event_id", "agent_id", "topic", "source", "source_revision"):
        _text(event[name], name)
    if event["agent_id"] != owner:
        raise NoosphereError("event belongs to another agent")
    if not isinstance(event["visibility"], str) or event["visibility"] not in _ALLOWED_VISIBILITY:
        raise NoosphereError("invalid visibility")
    for name in ("observed_at_ns", "recorded_at_ns", "valid_from_ns"):
        _integer(event[name], name)
    if event["valid_to_ns"] is not None:
        _integer(event["valid_to_ns"], "valid_to_ns")
        if event["valid_to_ns"] <= event["valid_from_ns"]:
            raise NoosphereError("valid_to_ns must be after valid_from_ns")
    if event["recorded_at_ns"] < event["observed_at_ns"]:
        raise NoosphereError("recorded_at_ns precedes observed_at_ns")
    if not isinstance(event["payload"], dict):
        raise NoosphereError("payload must be a JSON object")
    wire = _canonical(event)
    if len(wire.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise NoosphereError("event exceeds maximum size")
    # Reject NaN, tuples, mixed-key dictionaries, or other values which would
    # silently change on JSON roundtrip. Do not mutate the caller's input.
    if json.loads(wire) != event:
        raise NoosphereError("event is not a lossless JSON object")
    return json.loads(wire)


class LocalNoosphere:
    """Per-agent local SQLite event log; SQL writes are always atomic.

    Callers must protect the DB file and verify a trusted external anchor if
    malicious history rewriting matters. This is NOT an auth or encryption API.
    """

    def __init__(self, db_path, agent_id):
        self.agent_id = _text(agent_id, "agent_id")
        path = Path(db_path)
        if path.is_symlink():
            raise NoosphereError("database path cannot be a symbolic link")
        if path.is_dir():
            raise NoosphereError("database path cannot be a directory")
        # Explicit file path required. Not ':memory:' or URI addresses.
        if not path.name or str(db_path) == ":memory:":
            raise NoosphereError("a persistent SQLite database file is required")
        if not path.parent.is_dir():
            raise NoosphereError("database parent directory must exist")
        self._db = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("PRAGMA trusted_schema=OFF")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        if os.name == "posix":
            try:
                path.chmod(0o600)
            except OSError:
                pass  # Caller must enforce host filesystem policy.
        self._setup()

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _setup(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute("CREATE TABLE IF NOT EXISTS metadata (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
            self._db.execute("""CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                agent_id TEXT NOT NULL,
                recorded_at_ns INTEGER NOT NULL,
                observed_at_ns INTEGER NOT NULL,
                valid_from_ns INTEGER NOT NULL,
                valid_to_ns INTEGER,
                topic TEXT NOT NULL,
                visibility TEXT NOT NULL,
                wire_json TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL
            )""")
            self._db.execute("""CREATE TRIGGER IF NOT EXISTS noosphere_no_update
                BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'append-only events'); END""")
            self._db.execute("""CREATE TRIGGER IF NOT EXISTS noosphere_no_delete
                BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'append-only events'); END""")
            saved = dict(self._db.execute("SELECT k,v FROM metadata"))
            if not saved:
                self._db.execute("INSERT INTO metadata VALUES (?, ?)", ("schema", STORE_SCHEMA))
                self._db.execute("INSERT INTO metadata VALUES (?, ?)", ("owner", self.agent_id))
            elif saved != {"schema": STORE_SCHEMA, "owner": self.agent_id}:
                raise NoosphereError("database schema or agent owner does not match")
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def append(self, event, *, expected_head):
        """Append via CAS. Caller supplies expected previous hash; no retries hidden."""
        checked = _validate_event(event, self.agent_id)
        if not isinstance(expected_head, str) or len(expected_head) != 64:
            raise NoosphereError("expected_head must be the observed 64-character hash")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute("SELECT seq,event_hash,recorded_at_ns FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            previous = row["event_hash"] if row else _ZERO
            if expected_head != previous:
                raise NoosphereError("stale event head: CAS failed")
            if row and checked["recorded_at_ns"] < row["recorded_at_ns"]:
                raise NoosphereError("recorded_at_ns must be monotonically nondecreasing")
            event_hash = _sha({"previous": previous, "event": checked})
            self._db.execute(
                """INSERT INTO events(event_id,agent_id,recorded_at_ns,observed_at_ns,
                   valid_from_ns,valid_to_ns,topic,visibility,wire_json,prev_hash,event_hash)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (checked["event_id"], checked["agent_id"], checked["recorded_at_ns"],
                 checked["observed_at_ns"], checked["valid_from_ns"], checked["valid_to_ns"],
                 checked["topic"], checked["visibility"], _canonical(checked), previous, event_hash),
            )
            self._db.execute("COMMIT")
            return event_hash
        except sqlite3.IntegrityError as exc:
            self._db.execute("ROLLBACK")
            raise NoosphereError("duplicate event_id or database constraint violation") from exc
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def head(self):
        row = self._db.execute("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return row["event_hash"] if row else _ZERO

    def count(self):
        return self._db.execute("SELECT count(*) FROM events").fetchone()[0]

    def verify_chain(self, *, external_expected_head=None):
        """Detect accidental DB corruption. Without an independent anchor this is NOT tamper-proof."""
        prev = _ZERO
        last_recorded = -1
        expected_seq = 1
        sql = ("SELECT seq,event_id,agent_id,topic,visibility,observed_at_ns,"
               "recorded_at_ns,valid_from_ns,valid_to_ns,wire_json,prev_hash,event_hash "
               "FROM events ORDER BY seq")
        for row in self._db.execute(sql):
            seq = row["seq"]
            raw, prior, stored = row["wire_json"], row["prev_hash"], row["event_hash"]
            recorded = row["recorded_at_ns"]
            try:
                event = _validate_event(json.loads(raw), self.agent_id)
            except (ValueError, TypeError) as exc:
                raise NoosphereError(f"invalid event at row {seq}") from exc
            if seq != expected_seq:
                raise NoosphereError(f"noncontiguous sequence at row {seq}")
            for name in ("event_id", "agent_id", "topic", "visibility", "observed_at_ns",
                         "recorded_at_ns", "valid_from_ns", "valid_to_ns"):
                if event[name] != row[name]:
                    raise NoosphereError(f"indexed column {name} mismatches event at row {seq}")
            if prior != prev or _sha({"previous": prev, "event": event}) != stored:
                raise NoosphereError(f"hash chain mismatch at row {seq}")
            if recorded < last_recorded:
                raise NoosphereError(f"timestamp mismatch at row {seq}")
            prev = stored
            last_recorded = recorded
            expected_seq += 1
        if external_expected_head is not None and prev != external_expected_head:
            raise NoosphereError("hash head differs from independent external anchor")
        return {"schema": STORE_SCHEMA, "agent_id": self.agent_id, "count": expected_seq - 1, "head": prev, "integrity": "chain-verified-not-authenticated"}

    def dataset(self, *, topic=None, source=None, as_of_recorded_ns=None, valid_at_ns=None, include_nonpublic=False, limit=1000):
        """Read-only reproducible dataset projection; PRIVATE never exported by default."""
        if topic is not None:
            _text(topic, "topic")
        if source is not None:
            _text(source, "source")
        if as_of_recorded_ns is not None:
            _integer(as_of_recorded_ns, "as_of_recorded_ns")
        if valid_at_ns is not None:
            _integer(valid_at_ns, "valid_at_ns")
        if type(include_nonpublic) is not bool:
            raise NoosphereError("include_nonpublic must be boolean")
        _integer(limit, "limit", 1, 10_000)
        where = ["agent_id = ?"]
        args = [self.agent_id]
        if not include_nonpublic:
            where.append("visibility = 'PUBLIC'")
        if topic is not None:
            where.append("topic = ?")
            args.append(topic)
        if source is not None:
            where.append("json_extract(wire_json, '$.source') = ?")
            args.append(source)
        if as_of_recorded_ns is not None:
            where.append("recorded_at_ns <= ?")
            args.append(as_of_recorded_ns)
        if valid_at_ns is not None:
            where.append("valid_from_ns <= ? AND (valid_to_ns IS NULL OR valid_to_ns > ?)")
            args.extend((valid_at_ns, valid_at_ns))
        sql = "SELECT seq,wire_json,event_hash FROM events WHERE " + " AND ".join(where) + " ORDER BY seq LIMIT ?"
        args.append(limit)
        rows = self._db.execute(sql, args).fetchall()
        items = [{"seq": row["seq"], "event": json.loads(row["wire_json"]), "event_hash": row["event_hash"]} for row in rows]
        projection = {
            "schema": DATASET_SCHEMA,
            "agent_id": self.agent_id,
            "filters": {"topic": topic, "source": source, "as_of_recorded_ns": as_of_recorded_ns,
                        "valid_at_ns": valid_at_ns, "include_nonpublic": include_nonpublic, "limit": limit},
            "items": items,
        }
        return {"projection": copy.deepcopy(projection), "sha256": _sha(projection)}
