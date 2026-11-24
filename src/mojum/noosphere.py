"""Mojum Noosphere R0.1: local, append-only SQLite event memory.

This is single-owner local storage, not a distributed encrypted memory service.
A hash chain detects accidental or untrusted modification when its own head
is trusted independently; a local attacker with filesystem access can rewrite it.
"""

from __future__ import annotations

import copy
from contextlib import closing, contextmanager
import hashlib
import json
import os
import sqlite3
from pathlib import Path

EVENT_SCHEMA = "mojum.noosphere.event/1"
STORE_SCHEMA = "mojum.noosphere.sqlite/1"
DATASET_SCHEMA = "mojum.noosphere.dataset/1"
_ALLOWED_VISIBILITY = frozenset(("PUBLIC", "PRIVATE", "RESTRICTED"))
_INDEXED_EVENT_FIELDS = (
    "event_id", "agent_id", "topic", "visibility", "observed_at_ns",
    "recorded_at_ns", "valid_from_ns", "valid_to_ns",
)
_PROJECTION_COLUMNS = ",".join(("seq", "wire_json", "event_hash", *_INDEXED_EVENT_FIELDS))
EMPTY_HEAD = "0" * 64
_MAX_PAYLOAD_BYTES = 128_000


class NoosphereError(ValueError):
    """Invalid command, invalid event, or invalid local event store."""


def _text(value, field, max_length=256):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise NoosphereError(f"{field} requires a nonempty string of <= {max_length} characters")
    # Python str permits isolated UTF-16 surrogate code points, but our
    # persisted JSON / SQLite contract requires canonical UTF-8 throughout.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise NoosphereError(f"{field} must be valid UTF-8") from exc
    return value


def _integer(value, field, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise NoosphereError(f"{field} must be integer between {minimum} and {maximum}")
    return value


_MAX_JSON_DEPTH = 256


def _check_json_depth(value):
    """Bound JSON container nesting without recursion or an extra deep copy."""
    frames = [(iter((value,)), 0)]
    while frames:
        items, parent_depth = frames[-1]
        try:
            item = next(items)
        except StopIteration:
            frames.pop()
            continue
        if isinstance(item, (dict, list, tuple)):
            depth = parent_depth + 1
            if depth > _MAX_JSON_DEPTH:
                raise NoosphereError("event JSON nesting limit exceeded")
            children = item.values() if isinstance(item, dict) else item
            frames.append((iter(children), depth))


def _canonical(value):
    _check_json_depth(value)
    try:
        wire = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        # json.dumps(ensure_ascii=False) can return an unpaired surrogate.
        # Reject it before hashing or touching the durable SQLite journal.
        wire.encode("utf-8")
        return wire
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NoosphereError("event must contain canonical UTF-8 JSON") from exc


def _sha(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _unique_object(pairs):
    """Build one JSON object, refusing repeated keys.

    ``json.loads`` keeps only the last of duplicate keys, and the stored event
    hash is computed over that collapsed object.  A row that repeats a key
    therefore keeps a valid hash while the raw bytes stay ambiguous to any
    reader that resolves duplicates differently (for example SQLite
    ``json_extract``).  Persisted journals are only ever written from
    canonical JSON and never contain duplicates, so this is a fail-closed
    rejection of tampered or foreign history, not a format change.
    """
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise NoosphereError("duplicate JSON key in stored event")
        obj[key] = value
    return obj


def _loads_wire(raw):
    """Parse one stored ``wire_json`` value without collapsing duplicate keys."""
    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except NoosphereError:
        raise
    except (TypeError, ValueError, RecursionError) as exc:
        raise NoosphereError("invalid journal JSON") from exc


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


def _projection_event(row, owner):
    """Validate one selected row before exposing its decoded event.

    SQL indexes select candidates; they cannot authorize export of conflicting
    wire data. This checks each candidate, not the journal's full hash chain.
    """
    try:
        parsed = _loads_wire(row["wire_json"])
    except NoosphereError as exc:
        raise NoosphereError(f"invalid journal JSON: {exc}") from exc
    try:
        event = _validate_event(parsed, owner)
    except (ValueError, TypeError, RecursionError) as exc:
        raise NoosphereError(f"invalid event at row {row['seq']}") from exc
    for name in _INDEXED_EVENT_FIELDS:
        if event[name] != row[name]:
            raise NoosphereError(f"indexed column {name} mismatches event at row {row['seq']}")
    return event


@contextmanager
def _open_readonly(path):
    path = Path(path)
    if str(path) == ":memory:" or path.is_symlink() or not path.is_file():
        raise NoosphereError("expected an existing regular SQLite file")
    try:
        with closing(sqlite3.connect(path.absolute().as_uri() + "?mode=ro",
                                     uri=True, timeout=5.0)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA query_only=ON")
            yield db
    except sqlite3.Error as exc:
        raise NoosphereError("cannot read SQLite event journal") from exc


def _read_first_event(path, agent_id, *, external_expected_head=None):
    """Inspect existing bootstrap before any writable SQLite initialization."""
    path = Path(path)
    if path.is_symlink():
        raise NoosphereError("database path cannot be a symbolic link")
    if not path.is_file():
        return None
    with _open_readonly(path) as db:
        db.execute("BEGIN")
        tables = {row["name"] for row in db.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name IN ('metadata','events')",
        )}
        has_metadata = "metadata" in tables
        has_events = "events" in tables
        if has_metadata:
            saved = dict(db.execute("SELECT k,v FROM metadata"))
            if saved != {"schema": STORE_SCHEMA, "owner": agent_id}:
                if saved:
                    raise NoosphereError("database schema or agent owner does not match")
                raise NoosphereError("database metadata is missing or does not match")
        elif has_events:
            # A journal with events but no metadata is not a new database.
            # Refuse it before LocalNoosphere can repair the metadata table
            # through a writable connection and potentially alter the source.
            raise NoosphereError("database metadata is missing or does not match")
        if not has_events:
            return None
        first = db.execute("SELECT wire_json FROM events ORDER BY seq LIMIT 1").fetchone()
        if first is None:
            return None
        if external_expected_head is not None:
            # Check the tip in the same snapshot as the bootstrap. This
            # preflight cannot replace full replay verification after opening.
            tip = db.execute("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            if tip["event_hash"] != external_expected_head:
                raise NoosphereError("hash head differs from independent external anchor")
        try:
            event = _loads_wire(first["wire_json"])
        except NoosphereError as exc:
            raise NoosphereError(f"invalid bootstrap JSON: {exc}") from exc
        if type(event) is not dict:
            raise NoosphereError("expected an object for the first event")
        return event


def _verify_chain(db, agent_id, *, external_expected_head=None, strict_wire=False, inspect_event=None):
    prev = EMPTY_HEAD
    last_recorded = -1
    expected_seq = 1
    sql = ("SELECT seq,event_id,agent_id,topic,visibility,observed_at_ns,"
           "recorded_at_ns,valid_from_ns,valid_to_ns,wire_json,prev_hash,event_hash "
           "FROM events ORDER BY seq")
    for row in db.execute(sql):
        seq = row["seq"]
        raw, prior, stored = row["wire_json"], row["prev_hash"], row["event_hash"]
        recorded = row["recorded_at_ns"]
        try:
            parsed = _loads_wire(raw)
        except NoosphereError as exc:
            raise NoosphereError(f"invalid event at row {seq}: {exc}") from exc
        try:
            event = _validate_event(parsed, agent_id)
        except (ValueError, TypeError, RecursionError) as exc:
            raise NoosphereError(f"invalid event at row {seq}") from exc
        if strict_wire and (type(raw) is not str or _canonical(event) != raw):
            raise NoosphereError(f"noncanonical event at row {seq}")
        if seq != expected_seq:
            raise NoosphereError(f"noncontiguous sequence at row {seq}")
        for name in _INDEXED_EVENT_FIELDS:
            if event[name] != row[name]:
                raise NoosphereError(f"indexed column {name} mismatches event at row {seq}")
        if prior != prev or _sha({"previous": prev, "event": event}) != stored:
            raise NoosphereError(f"hash chain mismatch at row {seq}")
        if recorded < last_recorded:
            raise NoosphereError(f"timestamp mismatch at row {seq}")
        if inspect_event is not None:
            inspect_event(seq, event)
        prev = stored
        last_recorded = recorded
        expected_seq += 1
    if external_expected_head is not None and prev != external_expected_head:
        raise NoosphereError("hash head differs from independent external anchor")
    return {"schema": STORE_SCHEMA, "agent_id": agent_id, "count": expected_seq - 1, "head": prev, "integrity": "chain-verified-not-authenticated"}



class LocalNoosphere:
    """Per-agent local SQLite event log; SQL writes are always atomic.

    Callers must protect the DB file and verify a trusted external anchor if
    malicious history rewriting matters. This is NOT an auth or encryption API.
    """

    def __init__(self, db_path, agent_id, *, read_only=False):
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
        if type(read_only) is not bool:
            raise NoosphereError("read_only must be boolean")
        self.read_only = read_only
        if read_only and not path.is_file():
            raise NoosphereError("read-only journal must already exist")
        source = path.absolute().as_uri() + "?mode=ro" if read_only else str(path)
        self._db = sqlite3.connect(source, uri=read_only, timeout=5.0, isolation_level=None)
        # An exception inside __init__ never reaches __exit__/close() of a
        # context manager. In particular a wrong owner/schema must not leave
        # an unreachable live sqlite3.Connection for Python 3.13 GC to warn on.
        try:
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.execute("PRAGMA trusted_schema=OFF")
            if read_only:
                self._db.execute("PRAGMA query_only=ON")
                saved = dict(self._db.execute("SELECT k,v FROM metadata"))
                if saved != {"schema": STORE_SCHEMA, "owner": self.agent_id}:
                    raise NoosphereError("read-only journal owner or schema mismatch")
            else:
                self._db.execute("PRAGMA synchronous=FULL")
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA foreign_keys=ON")
                if os.name == "posix":
                    try:
                        path.chmod(0o600)
                    except OSError:
                        pass  # Caller must enforce host filesystem policy.
                self._setup()
        except BaseException:
            self._db.close()
            raise

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
        if self.read_only:
            raise NoosphereError("journal is read-only")
        checked = _validate_event(event, self.agent_id)
        if not isinstance(expected_head, str) or len(expected_head) != 64:
            raise NoosphereError("expected_head must be the observed 64-character hash")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute("SELECT seq,event_hash,recorded_at_ns FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            previous = row["event_hash"] if row else EMPTY_HEAD
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
        return row["event_hash"] if row else EMPTY_HEAD

    def count(self):
        return self._db.execute("SELECT count(*) FROM events").fetchone()[0]

    def verify_chain(self, *, external_expected_head=None, strict_wire=False):
        """Check persisted history and an optional independently recorded head."""
        return _verify_chain(self._db, self.agent_id, external_expected_head=external_expected_head,
                             strict_wire=strict_wire or self.read_only)

    def iter_events(self, *, topic=None, source=None, as_of_recorded_ns=None,
                    valid_at_ns=None, include_nonpublic=False, batch_size=128):
        """Yield journal rows in sequence order using bounded SQLite fetches.

        Streaming companion to :meth:`dataset`: callers can re-derive state
        without materializing the whole projection in memory. The filter and
        visibility semantics match ``dataset``. Each selected event is checked
        against its indexed columns before yield. This helper does not verify
        the hash chain by itself; callers that need integrity run
        :meth:`verify_chain` independently, as Aquarium replay does.
        """
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
        if type(batch_size) is not int or not 1 <= batch_size <= 10_000:
            raise NoosphereError("batch_size must be integer between 1 and 10000")
        where = ["agent_id = ?"]
        args = [self.agent_id]
        if not include_nonpublic:
            where.append("visibility = 'PUBLIC'")
        if topic is not None:
            where.append("topic = ?")
            args.append(topic)
        # Filter by source after duplicate-safe decoding. SQLite's
        # json_extract can otherwise hide an ambiguous row before validation.
        if as_of_recorded_ns is not None:
            where.append("recorded_at_ns <= ?")
            args.append(as_of_recorded_ns)
        if valid_at_ns is not None:
            where.append("valid_from_ns <= ? AND (valid_to_ns IS NULL OR valid_to_ns > ?)")
            args.extend((valid_at_ns, valid_at_ns))
        sql = ("SELECT " + _PROJECTION_COLUMNS + " FROM events WHERE "
               + " AND ".join(where) + " ORDER BY seq")
        cursor = self._db.execute(sql, args)
        try:
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    return
                for row in rows:
                    event = _projection_event(row, self.agent_id)
                    if source is not None:
                        if event["source"] != source:
                            continue
                    yield {"seq": row["seq"], "event": event,
                           "event_hash": row["event_hash"]}
        finally:
            cursor.close()

    def dataset(self, *, topic=None, source=None, as_of_recorded_ns=None, valid_at_ns=None, include_nonpublic=False, limit=1000):
        """Project checked candidates; PRIVATE is never exported by default.

        Candidate checks do not replace independent full-chain verification.
        """
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
        # Filter by source after duplicate-safe decoding. SQLite's
        # json_extract can otherwise hide an ambiguous row before validation.
        if as_of_recorded_ns is not None:
            where.append("recorded_at_ns <= ?")
            args.append(as_of_recorded_ns)
        if valid_at_ns is not None:
            where.append("valid_from_ns <= ? AND (valid_to_ns IS NULL OR valid_to_ns > ?)")
            args.extend((valid_at_ns, valid_at_ns))
        # The limit is applied after source filtering. Iterate rather than
        # materializing unbounded candidate rows when a source is requested.
        sql = ("SELECT " + _PROJECTION_COLUMNS + " FROM events WHERE "
               + " AND ".join(where) + " ORDER BY seq")
        rows = self._db.execute(sql, args)
        items = []
        try:
            for row in rows:
                event = _projection_event(row, self.agent_id)
                if source is not None:
                    if event["source"] != source:
                        continue
                items.append({"seq": row["seq"], "event": event,
                              "event_hash": row["event_hash"]})
                if len(items) >= limit:
                    break
        finally:
            rows.close()
        projection = {
            "schema": DATASET_SCHEMA,
            "agent_id": self.agent_id,
            "filters": {"topic": topic, "source": source, "as_of_recorded_ns": as_of_recorded_ns,
                        "valid_at_ns": valid_at_ns, "include_nonpublic": include_nonpublic, "limit": limit},
            "items": items,
        }
        return {"projection": copy.deepcopy(projection), "sha256": _sha(projection)}
