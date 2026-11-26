"""Opt-in durable SQLite queue for independent QA tasks.

This is a queue primitive, not a GitHub client, scheduler, or merge authority.
Only public PR metadata belongs in task payloads. Time is injected by callers.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import json
import re
import secrets
import sqlite3
from typing import Any

SCHEMA = "mojum.qa-work-queue/1"
_SCHEMA_VERSION = 1
_MAX_KEY = 160
_MAX_OWNER = 120
_MAX_KIND = 80
_MAX_PAYLOAD = 4096
_MAX_INTEGER = 2**63 - 1
_SHA = re.compile(r"^[0-9a-f]{40}$")
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")


class QueueError(ValueError):
    """Invalid queue input, schema, or state transition."""


@dataclass(frozen=True, slots=True)
class Task:
    task_id: int
    idempotency_key: str
    kind: str
    pr_number: int
    head_sha: str
    payload_json: str
    status: str
    attempts: int
    created_at: int
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: int | None = None


def _integer(value: object, name: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= _MAX_INTEGER:
        raise QueueError(f"{name} must be an integer in [{minimum}, {_MAX_INTEGER}]")
    return value


def _text(value: object, name: str, maximum: int, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise QueueError(f"{name} must be a non-empty bounded string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise QueueError(f"{name} has invalid characters or format")
    return value


def _payload(value: object) -> str:
    if type(value) is not dict:
        raise QueueError("payload must be a plain dict")
    # Payload is intentionally a tiny public-metadata envelope. Reject nested
    # values and arbitrary text to keep accidental private data out of the DB.
    allowed = {"source", "reason", "workflow"}
    # Bound cardinality and preflight exact native keys before hashing.
    if len(value) > len(allowed):
        raise QueueError("payload contains too many fields")
    keys = tuple(value.keys())
    if any(type(key) is not str or len(key) > 8 for key in keys):
        raise QueueError("payload keys must be short native strings")
    if set(keys) - allowed:
        raise QueueError("payload contains unsupported fields")
    clean: dict[str, str] = {}
    for key in keys:
        item = value[key]
        if key not in allowed or type(item) is not str or len(item) > 256:
            raise QueueError("payload values must be bounded plain strings")
        clean[key] = item
    encoded = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(encoded.encode("utf-8")) > _MAX_PAYLOAD:
        raise QueueError("payload budget exceeded")
    return encoded


def _connect(path: str) -> sqlite3.Connection:
    if type(path) is not str or not path or path == ":memory:" or path.startswith("file:"):
        raise QueueError("path must be a caller-owned persistent SQLite filename")
    db = sqlite3.connect(path, timeout=2.0, isolation_level=None)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=2000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
    except BaseException:
        db.close()
        raise
    return db


def _initialize(db: sqlite3.Connection) -> None:
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS queue_meta "
            "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL)"
        )
        row = db.execute("SELECT version FROM queue_meta WHERE singleton=1").fetchone()
        if row is None:
            db.execute("INSERT INTO queue_meta(singleton,version) VALUES(1,?)", (_SCHEMA_VERSION,))
        elif type(row["version"]) is not int or row["version"] != _SCHEMA_VERSION:
            raise QueueError("unsupported queue schema version")
        db.execute(
            """CREATE TABLE IF NOT EXISTS qa_tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                pr_number INTEGER NOT NULL CHECK(pr_number > 0),
                head_sha TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','leased','done','failed')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                created_at INTEGER NOT NULL,
                lease_owner TEXT,
                lease_token TEXT,
                lease_expires_at INTEGER,
                CHECK((status='leased' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
                   OR (status!='leased' AND lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL))
            )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS qa_tasks_ready ON qa_tasks(status, created_at, task_id)")
        db.commit()
    except Exception:
        db.rollback()
        raise


def _task(row: sqlite3.Row) -> Task:
    return Task(**{key: row[key] for key in Task.__dataclass_fields__})


def enqueue(path: str, *, idempotency_key: str, kind: str, pr_number: int,
            head_sha: str, created_at: int, payload: dict[str, str] | None = None) -> Task:
    """Insert once; repeated idempotency keys return the original task unchanged."""
    key = _text(idempotency_key, "idempotency_key", _MAX_KEY, _KEY)
    task_kind = _text(kind, "kind", _MAX_KIND, _KEY)
    number = _integer(pr_number, "pr_number", 1)
    sha = _text(head_sha, "head_sha", 40, _SHA)
    now = _integer(created_at, "created_at")
    encoded = _payload({} if payload is None else payload)
    with closing(_connect(path)) as db:
        _initialize(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                """INSERT OR IGNORE INTO qa_tasks
                (idempotency_key,kind,pr_number,head_sha,payload_json,status,created_at)
                VALUES(?,?,?,?,?,'queued',?)""",
                (key, task_kind, number, sha, encoded, now),
            )
            row = db.execute("SELECT * FROM qa_tasks WHERE idempotency_key=?", (key,)).fetchone()
            db.commit()
            return _task(row)
        except Exception:
            db.rollback()
            raise


def claim(path: str, *, owner: str, now: int, lease_ms: int) -> Task | None:
    """Atomically claim the oldest queued or expired lease; exactly one concurrent winner."""
    worker = _text(owner, "owner", _MAX_OWNER, _KEY)
    clock = _integer(now, "now")
    duration = _integer(lease_ms, "lease_ms", 1)
    if clock > _MAX_INTEGER - duration:
        raise QueueError("lease expiry overflow")
    token = secrets.token_hex(32)
    with closing(_connect(path)) as db:
        _initialize(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute(
                """SELECT * FROM qa_tasks
                WHERE status='queued' OR (status='leased' AND lease_expires_at<=?)
                ORDER BY CASE WHEN status='queued' THEN 0 ELSE 1 END, created_at, task_id LIMIT 1""", (clock,)
            ).fetchone()
            if row is None:
                db.commit()
                return None
            db.execute(
                """UPDATE qa_tasks SET status='leased',lease_owner=?,lease_token=?,
                lease_expires_at=?,attempts=attempts+1 WHERE task_id=?""",
                (worker, token, clock + duration, row["task_id"]),
            )
            claimed = db.execute("SELECT * FROM qa_tasks WHERE task_id=?", (row["task_id"],)).fetchone()
            db.commit()
            return _task(claimed)
        except Exception:
            db.rollback()
            raise


def _lease_arguments(task_id: int, owner: str, token: str, now: int) -> tuple[int, str, str, int]:
    """Validate caller arguments before any connection or schema initialization."""
    ident = _integer(task_id, "task_id", 1)
    worker = _text(owner, "owner", _MAX_OWNER, _KEY)
    lease = _text(token, "lease_token", 64, re.compile(r"^[0-9a-f]{64}$"))
    clock = _integer(now, "now")
    return ident, worker, lease, clock


def _owned(db: sqlite3.Connection, task_id: int, owner: str, token: str, now: int) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM qa_tasks WHERE task_id=? AND status='leased' AND lease_owner=? "
        "AND lease_token=? AND lease_expires_at>?", (task_id, owner, token, now)
    ).fetchone()
    if row is None:
        raise QueueError("lease is stale, expired, or not owned by caller")
    return row


def heartbeat(path: str, *, task_id: int, owner: str, lease_token: str,
              now: int, lease_ms: int) -> Task:
    ident, worker, token, clock = _lease_arguments(task_id, owner, lease_token, now)
    duration = _integer(lease_ms, "lease_ms", 1)
    if clock > _MAX_INTEGER - duration:
        raise QueueError("lease expiry overflow")
    with closing(_connect(path)) as db:
        _initialize(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            current = _owned(db, ident, worker, token, clock)
            expiry = max(current["lease_expires_at"], clock + duration)
            db.execute("UPDATE qa_tasks SET lease_expires_at=? WHERE task_id=?",
                       (expiry, ident))
            row = db.execute("SELECT * FROM qa_tasks WHERE task_id=?", (ident,)).fetchone()
            db.commit()
            return _task(row)
        except Exception:
            db.rollback()
            raise


def finish(path: str, *, task_id: int, owner: str, lease_token: str,
           now: int, success: bool) -> Task:
    if type(success) is not bool:
        raise QueueError("success must be bool")
    ident, worker, token, clock = _lease_arguments(task_id, owner, lease_token, now)
    with closing(_connect(path)) as db:
        _initialize(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            _owned(db, ident, worker, token, clock)
            db.execute(
                """UPDATE qa_tasks SET status=?,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL
                WHERE task_id=?""", ("done" if success else "failed", ident)
            )
            row = db.execute("SELECT * FROM qa_tasks WHERE task_id=?", (ident,)).fetchone()
            db.commit()
            return _task(row)
        except Exception:
            db.rollback()
            raise


def release(path: str, *, task_id: int, owner: str, lease_token: str, now: int) -> Task:
    """Return a currently leased task to the queue without erasing its attempt count."""
    ident, worker, token, clock = _lease_arguments(task_id, owner, lease_token, now)
    with closing(_connect(path)) as db:
        _initialize(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            _owned(db, ident, worker, token, clock)
            db.execute(
                """UPDATE qa_tasks SET status='queued',lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL
                WHERE task_id=?""", (ident,)
            )
            row = db.execute("SELECT * FROM qa_tasks WHERE task_id=?", (ident,)).fetchone()
            db.commit()
            return _task(row)
        except Exception:
            db.rollback()
            raise
