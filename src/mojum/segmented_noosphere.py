"""R0.16 experimental logical segment journal, inside one PRIVATE SQLite file.

This is an opt-in *different* store/format from LocalNoosphere sqlite/1.
Every event and immutable segment-start row shares a single SQLite
transaction; segments link to the previous segment's final event hash.
Nothing ever deletes, rewrites or migrates existing user histories.

This first version solves atomic segments and >100k events, NOT separate
physical storage, fault tolerance against loss of the sole SQLite file,
cryptographic authentication, or continuous Aquarium beyond its tick cap.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .noosphere import (
    DATASET_SCHEMA, EVENT_SCHEMA, NoosphereError, _ZERO,
    _canonical, _integer, _sha, _text, _validate_event,
)

STORE_SCHEMA = "mojum.noosphere.segmented-sqlite/1"
SEGMENT_SCHEMA = "mojum.noosphere.segment-start/1"
EVENT_HASH_SCHEMA = "mojum.noosphere.segmented-event-hash/1"
MAX_BATCH = 256
MAX_SEGMENT_SIZE = 8192
MIN_SEGMENT_SIZE = 64
MAX_JOURNAL_EVENTS = 262_144


def _hash_event(event, *, seq, segment_index, previous):
    envelope = {
        "schema": EVENT_HASH_SCHEMA, "seq": seq,
        "segment_index": segment_index,
        "previous": previous, "event": event,
    }
    return hashlib.sha256(_canonical(envelope).encode("utf-8")).hexdigest()


class SegmentedNoosphere:
    """One owner-only SQLite source, multiple *logical* immutable segments.

    LocalNoosphere(/1) remains a separate opt-in backend. This is not
    distributed replication or a multi-file log. A SQLite transaction
    commits segment boundary metadata with the first event in that
    segment, so no half-created segment is ever visible.
    """

    def __init__(self, db_path, agent_id, *, segment_size=4096):
        self.agent_id = _text(agent_id, "agent_id")
        if (type(segment_size) is not int
                or not MIN_SEGMENT_SIZE <= segment_size <= MAX_SEGMENT_SIZE):
            raise NoosphereError("segment_size must be integer in 64..8192")
        self.segment_size = segment_size
        path = Path(db_path)
        if (path.is_symlink() or path.is_dir() or not path.name
                or str(db_path) == ":memory:" or not path.parent.is_dir()):
            raise NoosphereError("segmented database needs an ordinary local file")
        self._db = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.execute("PRAGMA trusted_schema=OFF")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            if os.name == "posix":
                try:
                    path.chmod(0o600)
                except OSError:
                    pass  # Not a replacement for owner filesystem policy.
            self._setup()
        except BaseException:
            self._db.close()
            raise

    def _setup(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS metadata (k TEXT PRIMARY KEY, v TEXT NOT NULL)"
            )
            expected = {
                "schema": STORE_SCHEMA,
                "owner": self.agent_id,
                "segment_size": str(self.segment_size),
            }
            saved = dict(self._db.execute("SELECT k,v FROM metadata"))
            if saved and saved != expected:
                raise NoosphereError("wrong owner/schema/segment size; no automatic migration")
            if not saved:
                self._db.executemany(
                    "INSERT INTO metadata(k,v) VALUES (?,?)", sorted(expected.items())
                )
            self._db.execute("""CREATE TABLE IF NOT EXISTS segments (
                segment_index INTEGER PRIMARY KEY,
                first_seq INTEGER UNIQUE NOT NULL,
                prior_segment_head TEXT NOT NULL,
                schema TEXT NOT NULL
            )""")
            self._db.execute("""CREATE TABLE IF NOT EXISTS segmented_events (
                seq INTEGER PRIMARY KEY,
                event_id TEXT UNIQUE NOT NULL,
                agent_id TEXT NOT NULL,
                recorded_at_ns INTEGER NOT NULL,
                topic TEXT NOT NULL,
                visibility TEXT NOT NULL,
                wire_json TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                segment_index INTEGER NOT NULL REFERENCES segments(segment_index)
            )""")
            self._db.execute("""CREATE TRIGGER IF NOT EXISTS no_segment_update
                BEFORE UPDATE ON segmented_events BEGIN
                    SELECT RAISE(ABORT, 'append-only segmented events'); END""")
            self._db.execute("""CREATE TRIGGER IF NOT EXISTS no_segment_delete
                BEFORE DELETE ON segmented_events BEGIN
                    SELECT RAISE(ABORT, 'append-only segmented events'); END""")
            self._db.execute("""CREATE TRIGGER IF NOT EXISTS no_boundary_update
                BEFORE UPDATE ON segments BEGIN
                    SELECT RAISE(ABORT, 'immutable segment boundary'); END""")
            self._db.execute("""CREATE TRIGGER IF NOT EXISTS no_boundary_delete
                BEFORE DELETE ON segments BEGIN
                    SELECT RAISE(ABORT, 'immutable segment boundary'); END""")
            self._db.execute("""CREATE TRIGGER IF NOT EXISTS no_segment_metadata_update
                BEFORE UPDATE ON metadata BEGIN
                    SELECT RAISE(ABORT, 'immutable journal metadata'); END""")
            self._db.execute("""CREATE TRIGGER IF NOT EXISTS no_segment_metadata_delete
                BEFORE DELETE ON metadata BEGIN
                    SELECT RAISE(ABORT, 'immutable journal metadata'); END""")
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _tip(self):
        return self._db.execute(
            "SELECT seq,event_hash,recorded_at_ns FROM segmented_events "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()

    def count(self):
        return self._db.execute(
            "SELECT count(*) FROM segmented_events"
        ).fetchone()[0]

    def head(self):
        row = self._tip()
        return row["event_hash"] if row is not None else _ZERO

    def append(self, event, *, expected_head):
        """Drop-in single-frame CAS semantics; one physical commit per call."""
        return self.append_many([event], expected_head=expected_head)

    def append_many(self, events, *, expected_head):
        """Bounded atomic batch: zero or all events, including new boundaries.

        Multiple writers are serialized by BEGIN IMMEDIATE, and the expected
        *global* head CAS rejects stale writers even when they race rollover.
        """
        if (type(events) not in (list, tuple)
                or not 1 <= len(events) <= MAX_BATCH):
            raise NoosphereError("append_many requires 1..256 events")
        checked = [_validate_event(e, self.agent_id) for e in events]
        if (type(expected_head) is not str or len(expected_head) != 64
                or any(char not in "0123456789abcdef" for char in expected_head)):
            raise NoosphereError("expected_head must be lowercase SHA256")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            tip = self._tip()
            seq = tip["seq"] if tip is not None else 0
            prev = tip["event_hash"] if tip is not None else _ZERO
            prev_recorded = tip["recorded_at_ns"] if tip is not None else -1
            if prev != expected_head:
                raise NoosphereError("stale segmented head: CAS failed")
            if seq + len(checked) > MAX_JOURNAL_EVENTS:
                raise NoosphereError("segmented journal event limit reached")
            for event in checked:
                if event["recorded_at_ns"] < prev_recorded:
                    raise NoosphereError("segmented timestamp regression")
                next_seq = seq + 1
                seg = (next_seq - 1) // self.segment_size
                if (next_seq - 1) % self.segment_size == 0:
                    self._db.execute(
                        "INSERT INTO segments("
                        "segment_index,first_seq,prior_segment_head,schema)"
                        " VALUES(?,?,?,?)",
                        (seg, next_seq, prev, SEGMENT_SCHEMA),
                    )
                event_hash = _hash_event(
                    event, seq=next_seq, segment_index=seg, previous=prev
                )
                self._db.execute(
                    "INSERT INTO segmented_events("
                    "seq,event_id,agent_id,recorded_at_ns,topic,visibility,"
                    "wire_json,prev_hash,event_hash,segment_index)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (next_seq, event["event_id"], event["agent_id"],
                     event["recorded_at_ns"], event["topic"],
                     event["visibility"], _canonical(event), prev,
                     event_hash, seg),
                )
                seq, prev, prev_recorded = (
                    next_seq, event_hash, event["recorded_at_ns"],
                )
            self._db.execute("COMMIT")
            return prev
        except sqlite3.IntegrityError as exc:
            self._db.execute("ROLLBACK")
            raise NoosphereError(
                "duplicate ID, segment boundary race or journal constraint"
            ) from exc
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def verify_chain(self, *, external_expected_head=None):
        """Stream all events and all segment anchors; zero event list in RAM."""
        actual_meta = dict(self._db.execute("SELECT k,v FROM metadata"))
        if actual_meta != {
            "schema": STORE_SCHEMA, "owner": self.agent_id,
            "segment_size": str(self.segment_size),
        }:
            raise NoosphereError("segmented journal metadata mismatch")
        boundary = self._db.execute(
            "SELECT segment_index,first_seq,prior_segment_head,schema "
            "FROM segments ORDER BY segment_index"
        )
        pending = boundary.fetchone()
        previous = _ZERO
        last_recorded = -1
        expected_seq = 1
        last_seg = -1
        rows = self._db.execute(
            "SELECT seq,event_id,agent_id,recorded_at_ns,topic,visibility,"
            "wire_json,prev_hash,event_hash,segment_index "
            "FROM segmented_events ORDER BY seq"
        )
        try:
            for row in rows:
                seq = row["seq"]
                seg = (expected_seq - 1) // self.segment_size
                if seq != expected_seq or row["segment_index"] != seg:
                    raise NoosphereError("segment journal sequence discontinuity")
                if seg != last_seg:
                    if (pending is None or pending["schema"] != SEGMENT_SCHEMA
                            or pending["segment_index"] != seg
                            or pending["first_seq"] != expected_seq
                            or pending["prior_segment_head"] != previous):
                        raise NoosphereError("missing, forged or mislinked segment")
                    pending = boundary.fetchone()
                    last_seg = seg
                try:
                    raw = row["wire_json"]
                    event = _validate_event(json.loads(raw), self.agent_id)
                except (ValueError, TypeError) as exc:
                    raise NoosphereError("invalid serialized segmented event") from exc
                if _canonical(event) != raw:
                    raise NoosphereError("noncanonical segmented event encoding")
                for col in ("event_id", "agent_id", "recorded_at_ns",
                            "topic", "visibility"):
                    if row[col] != event[col]:
                        raise NoosphereError("event materialization disagrees with event")
                if (row["prev_hash"] != previous
                        or row["event_hash"] != _hash_event(
                            event, seq=seq, segment_index=seg, previous=previous
                        )
                        or event["recorded_at_ns"] < last_recorded):
                    raise NoosphereError("segmented hash/timestamp chain mismatch")
                previous, last_recorded, expected_seq = (
                    row["event_hash"], event["recorded_at_ns"], expected_seq+1
                )
            if pending is not None:
                raise NoosphereError("orphan or extra segment metadata")
        finally:
            rows.close()
            boundary.close()
        if external_expected_head is not None and previous != external_expected_head:
            raise NoosphereError("segmented head differs from independent anchor")
        return {
            "schema": STORE_SCHEMA, "agent_id": self.agent_id,
            "count": expected_seq - 1, "head": previous,
            "segments": (last_seg + 1),
            "integrity": "linked-segments-verified-not-authenticated",
        }

    def event_at(self, seq):
        _integer(seq, "seq", 1)
        row = self._db.execute(
            "SELECT seq,wire_json,event_hash,segment_index FROM segmented_events "
            "WHERE seq = ? AND agent_id = ?", (seq, self.agent_id),
        ).fetchone()
        if row is None:
            raise NoosphereError("segmented event anchor missing")
        return {
            "seq": row["seq"], "event": json.loads(row["wire_json"]),
            "event_hash": row["event_hash"],
        }

    def iter_events(self, *, include_nonpublic=False, after_seq=0,
                    batch_size=128):
        if type(include_nonpublic) is not bool:
            raise NoosphereError("include_nonpublic must be boolean")
        _integer(after_seq, "after_seq")
        _integer(batch_size, "batch_size", 1, 1024)
        sql = (
            "SELECT seq,wire_json,event_hash FROM segmented_events "
            "WHERE agent_id = ? AND seq > ?"
            + ("" if include_nonpublic else " AND visibility = 'PUBLIC'")
            + " ORDER BY seq"
        )
        cursor = self._db.execute(sql, (self.agent_id, after_seq))
        try:
            while True:
                group = cursor.fetchmany(batch_size)
                if not group:
                    return
                for row in group:
                    yield {
                        "seq": row["seq"], "event": json.loads(row["wire_json"]),
                        "event_hash": row["event_hash"],
                    }
        finally:
            cursor.close()

    def dataset(self, *, topic=None, source=None, as_of_recorded_ns=None,
                valid_at_ns=None, include_nonpublic=False, limit=1000):
        """Same 10k-max bounded filtered projection as LocalNoosphere."""
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
        _integer(limit, "limit", 1, 10000)
        where = ["agent_id = ?"]
        params = [self.agent_id]
        if not include_nonpublic:
            where.append("visibility = 'PUBLIC'")
        if topic is not None:
            where.append("topic = ?")
            params.append(topic)
        if source is not None:
            where.append("json_extract(wire_json, '$.source') = ?")
            params.append(source)
        if as_of_recorded_ns is not None:
            where.append("recorded_at_ns <= ?")
            params.append(as_of_recorded_ns)
        if valid_at_ns is not None:
            where.append(
                "json_extract(wire_json,'$.valid_from_ns') <= ? AND "
                "(json_extract(wire_json,'$.valid_to_ns') IS NULL OR "
                "json_extract(wire_json,'$.valid_to_ns') > ?)"
            )
            params.extend((valid_at_ns, valid_at_ns))
        params.append(limit)
        rows = self._db.execute(
            "SELECT seq,wire_json,event_hash FROM segmented_events WHERE "
            + " AND ".join(where) + " ORDER BY seq LIMIT ?", params,
        ).fetchall()
        items = [
            {"seq": r["seq"], "event": json.loads(r["wire_json"]),
             "event_hash": r["event_hash"]}
            for r in rows
        ]
        projection = {
            "schema": DATASET_SCHEMA, "agent_id": self.agent_id,
            "filters": {"topic": topic, "source": source,
                        "as_of_recorded_ns": as_of_recorded_ns,
                        "valid_at_ns": valid_at_ns,
                        "include_nonpublic": include_nonpublic, "limit": limit},
            "items": items,
        }
        return {"projection": projection, "sha256": _sha(projection)}
