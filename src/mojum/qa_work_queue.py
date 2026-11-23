"""Durable lease-fenced SQLite queue for Mojum QA (stdlib only)."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import secrets
import sqlite3
import threading
import time
from typing import Any

SCHEMA = "mojum.qa-work-queue/1"
MAX_PAYLOAD_BYTES = 65_536
MAX_AUDIT_ROWS = 10_000


class QueueError(ValueError):
    """Invalid queue operation or configuration."""


class IdempotencyConflict(QueueError):
    """An idempotency key was reused for different work."""


class LeaseLost(QueueError):
    """The lease expired or was replaced."""


@dataclass(frozen=True)
class Job:
    id: int
    idempotency_key: str
    kind: str
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    available_at: float
    created_at: float
    updated_at: float
    lease_owner: str | None = None
    lease_until: float | None = None
    last_error_code: str | None = None


@dataclass(frozen=True)
class Lease:
    job: Job
    token: str
    expires_at: float


def _text(value: object, name: str, limit: int) -> str:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise QueueError(f"{name} must be nonblank text of at most {limit} characters")
    return value.strip()


def _number(value: object, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QueueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise QueueError(f"{name} is outside the permitted range")
    return result


def _validate(
    value: object,
    depth: int = 0,
    budget: list[int] | None = None,
    text_budget: list[int] | None = None,
) -> None:
    if budget is None:
        budget = [10_000]
    if text_budget is None:
        text_budget = [MAX_PAYLOAD_BYTES]
    budget[0] -= 1
    if budget[0] < 0 or depth > 32:
        raise QueueError("payload exceeds structural limits")
    if type(value) is str:
        # Check character count first: UTF-8 bytes can never be fewer than
        # code points, so oversized strings are rejected before encoding.
        if len(value) > text_budget[0]:
            raise QueueError("payload text exceeds byte budget")
        size = len(value.encode("utf-8"))
        if size > text_budget[0]:
            raise QueueError("payload text exceeds byte budget")
        text_budget[0] -= size
        return
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise QueueError("payload numbers must be finite")
        return
    if type(value) is list:
        for item in value:
            _validate(item, depth + 1, budget, text_budget)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise QueueError("payload keys must be plain strings")
            if len(key) > text_budget[0]:
                raise QueueError("payload text exceeds byte budget")
            key_size = len(key.encode("utf-8"))
            if key_size > text_budget[0]:
                raise QueueError("payload text exceeds byte budget")
            text_budget[0] -= key_size
            _validate(item, depth + 1, budget, text_budget)
        return
    raise QueueError("payload must contain exact native JSON values")


def _payload(value: object) -> tuple[str, dict[str, Any]]:
    if type(value) is not dict:
        raise QueueError("payload must be a plain dictionary")
    _validate(value, text_budget=[MAX_PAYLOAD_BYTES])
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        raise QueueError("payload is not bounded JSON data") from exc
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise QueueError("payload exceeds 65536 UTF-8 bytes")
    return encoded, decoded


class QAWorkQueue:
    """Atomic queue with expiring leases, idempotency and bounded audit history."""

    def __init__(self, database: str, *, busy_timeout: float = 5.0,
                 audit_limit: int = MAX_AUDIT_ROWS) -> None:
        self.database = _text(database, "database", 4096)
        timeout = _number(busy_timeout, "busy_timeout", 0.01, 60)
        if isinstance(audit_limit, bool) or not isinstance(audit_limit, int) or not 100 <= audit_limit <= MAX_AUDIT_ROWS:
            raise QueueError(f"audit_limit must be between 100 and {MAX_AUDIT_ROWS}")
        self.audit_limit = audit_limit
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.database, timeout=timeout,
                                   isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        has_jobs = self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='qa_jobs'"
        ).fetchone() is not None
        has_meta = self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='qa_meta'"
        ).fetchone() is not None
        if has_jobs and not has_meta:
            self._db.close()
            raise QueueError("unversioned queue database; refusing implicit adoption")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS qa_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        version = self._db.execute(
            "SELECT value FROM qa_meta WHERE key='schema_version'"
        ).fetchone()
        if version is None:
            self._db.execute(
                "INSERT INTO qa_meta(key,value) VALUES('schema_version',?)", (SCHEMA,)
            )
        elif version["value"] != SCHEMA:
            self._db.close()
            raise QueueError("unsupported queue schema version")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS qa_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','leased','succeeded','failed')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                max_attempts INTEGER NOT NULL CHECK(max_attempts BETWEEN 1 AND 100),
                available_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                lease_owner TEXT,
                lease_token TEXT,
                lease_until REAL,
                last_error_code TEXT,
                CHECK((status='leased' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
                   OR (status<>'leased' AND lease_owner IS NULL AND lease_token IS NULL AND lease_until IS NULL))
            );
            CREATE INDEX IF NOT EXISTS qa_jobs_ready_idx
                ON qa_jobs(status,available_at,created_at,id);
            CREATE TABLE IF NOT EXISTS qa_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES qa_jobs(id) ON DELETE CASCADE,
                event TEXT NOT NULL, at REAL NOT NULL, worker TEXT, error_code TEXT
            );
            CREATE INDEX IF NOT EXISTS qa_audit_job_idx ON qa_audit(job_id,sequence);
        """)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "QAWorkQueue":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _id(job_id: object) -> int:
        if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
            raise QueueError("job_id must be a positive integer")
        return job_id

    @staticmethod
    def _now(now: float | None) -> float:
        return time.time() if now is None else _number(now, "now", 0, 10_000_000_000)

    def _audit(self, job_id: int, event: str, at: float,
               worker: str | None = None, error: str | None = None) -> None:
        self._db.execute(
            "INSERT INTO qa_audit(job_id,event,at,worker,error_code) VALUES(?,?,?,?,?)",
            (job_id, event, at, worker, error))
        self._db.execute(
            "DELETE FROM qa_audit WHERE sequence <= (SELECT COALESCE(MAX(sequence),0)-? FROM qa_audit)",
            (self.audit_limit,))

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(row["id"], row["idempotency_key"], row["kind"],
                   json.loads(row["payload_json"]), row["status"], row["attempts"],
                   row["max_attempts"], row["available_at"], row["created_at"],
                   row["updated_at"], row["lease_owner"],
                   row["lease_until"], row["last_error_code"])

    def get(self, job_id: int) -> Job:
        job_id = self._id(job_id)
        with self._lock:
            row = self._db.execute("SELECT * FROM qa_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise QueueError("job does not exist")
        return self._job(row)

    def enqueue(self, idempotency_key: str, kind: str, payload: dict[str, Any], *,
                max_attempts: int = 3, available_at: float | None = None,
                now: float | None = None) -> tuple[Job, bool]:
        key = _text(idempotency_key, "idempotency_key", 200)
        work_kind = _text(kind, "kind", 100)
        encoded, _ = _payload(payload)
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 100:
            raise QueueError("max_attempts must be an integer from 1 to 100")
        stamp = self._now(now)
        ready = stamp if available_at is None else _number(available_at, "available_at", 0, 10_000_000_000)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                cur = self._db.execute(
                    "INSERT OR IGNORE INTO qa_jobs(idempotency_key,kind,payload_json,status,attempts,max_attempts,available_at,created_at,updated_at) "
                    "VALUES(?,?,?,'queued',0,?,?,?,?)",
                    (key, work_kind, encoded, max_attempts, ready, stamp, stamp))
                row = self._db.execute("SELECT * FROM qa_jobs WHERE idempotency_key=?", (key,)).fetchone()
                if row is None:
                    raise QueueError("insert could not be verified")
                if row["kind"] != work_kind or row["payload_json"] != encoded or row["max_attempts"] != max_attempts:
                    raise IdempotencyConflict("idempotency key already belongs to different work")
                inserted = cur.rowcount == 1
                if inserted:
                    self._audit(row["id"], "ENQUEUED", stamp)
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return self.get(row["id"]), inserted

    def _expire(self, stamp: float) -> None:
        rows = self._db.execute(
            "SELECT id,attempts,max_attempts FROM qa_jobs WHERE status='leased' AND lease_until<=?",
            (stamp,)).fetchall()
        for row in rows:
            final = row["attempts"] >= row["max_attempts"]
            self._db.execute(
                "UPDATE qa_jobs SET status=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL,updated_at=?,last_error_code=? WHERE id=?",
                ("failed" if final else "queued", stamp, "LEASE_EXPIRED" if final else None, row["id"]))
            self._audit(row["id"], "LEASE_EXPIRED_FINAL" if final else "LEASE_EXPIRED_REQUEUED", stamp)

    def claim(self, worker: str, *, lease_seconds: float = 60,
              now: float | None = None) -> Lease | None:
        owner = _text(worker, "worker", 120)
        duration = _number(lease_seconds, "lease_seconds", 1, 86_400)
        stamp = self._now(now)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._expire(stamp)
                row = self._db.execute(
                    "SELECT * FROM qa_jobs WHERE status='queued' AND available_at<=? AND attempts<max_attempts "
                    "ORDER BY available_at,created_at,id LIMIT 1", (stamp,)).fetchone()
                if row is None:
                    self._db.commit()
                    return None
                token, expires = secrets.token_urlsafe(32), stamp + duration
                changed = self._db.execute(
                    "UPDATE qa_jobs SET status='leased',attempts=attempts+1,lease_owner=?,lease_token=?,lease_until=?,updated_at=? "
                    "WHERE id=? AND status='queued'",
                    (owner, token, expires, stamp, row["id"])).rowcount
                if changed != 1:
                    raise QueueError("atomic claim failed")
                self._audit(row["id"], "CLAIMED", stamp, owner)
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return Lease(self.get(row["id"]), token, expires)

    def heartbeat(self, job_id: int, token: str, *, lease_seconds: float = 60,
                  now: float | None = None) -> float:
        job_id = self._id(job_id)
        token = _text(token, "lease token", 200)
        duration, stamp = _number(lease_seconds, "lease_seconds", 1, 86_400), self._now(now)
        expires = stamp + duration
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                changed = self._db.execute(
                    "UPDATE qa_jobs SET lease_until=?,updated_at=? WHERE id=? AND status='leased' AND lease_token=? AND lease_until>?",
                    (expires, stamp, job_id, token, stamp)).rowcount
                if changed != 1:
                    raise LeaseLost("lease is expired, stale, or owned by another worker")
                owner = self._db.execute("SELECT lease_owner FROM qa_jobs WHERE id=?", (job_id,)).fetchone()["lease_owner"]
                self._audit(job_id, "HEARTBEAT", stamp, owner)
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return expires

    def _transition(self, job_id: int, token: str, status: str, event: str, *,
                    error_code: str | None = None, available_at: float | None = None,
                    now: float | None = None) -> Job:
        job_id, token, stamp = self._id(job_id), _text(token, "lease token", 200), self._now(now)
        if error_code is not None:
            error_code = _text(error_code, "error_code", 120)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT * FROM qa_jobs WHERE id=? AND status='leased' AND lease_token=? AND lease_until>?",
                    (job_id, token, stamp)).fetchone()
                if row is None:
                    raise LeaseLost("lease is expired, stale, or owned by another worker")
                next_status = status
                if status == "queued" and row["attempts"] >= row["max_attempts"]:
                    next_status, event = "failed", "RETRY_EXHAUSTED"
                ready = row["available_at"] if available_at is None else available_at
                self._db.execute(
                    "UPDATE qa_jobs SET status=?,available_at=?,updated_at=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL,last_error_code=? WHERE id=?",
                    (next_status, ready, stamp, error_code, job_id))
                self._audit(job_id, event, stamp, row["lease_owner"], error_code)
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return self.get(job_id)

    def complete(self, job_id: int, token: str, *, now: float | None = None) -> Job:
        return self._transition(job_id, token, "succeeded", "SUCCEEDED", now=now)

    def fail(self, job_id: int, token: str, *, error_code: str = "WORK_FAILED",
             now: float | None = None) -> Job:
        return self._transition(job_id, token, "failed", "FAILED", error_code=error_code, now=now)

    def retry(self, job_id: int, token: str, *, delay_seconds: float = 0,
              error_code: str = "RETRYABLE_FAILURE", now: float | None = None) -> Job:
        stamp = self._now(now)
        delay = _number(delay_seconds, "delay_seconds", 0, 86_400)
        return self._transition(job_id, token, "queued", "REQUEUED",
                                error_code=error_code, available_at=stamp + delay, now=stamp)

    def list_jobs(self, *, status: str | None = None, limit: int = 100) -> tuple[Job, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise QueueError("limit must be an integer from 1 to 500")
        if status is not None and status not in {"queued", "leased", "succeeded", "failed"}:
            raise QueueError("unsupported status filter")
        sql, args = "SELECT * FROM qa_jobs", ()
        if status is not None:
            sql, args = sql + " WHERE status=?", (status,)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY created_at,id LIMIT ?", args + (limit,)).fetchall()
        return tuple(self._job(row) for row in rows)

    def audit(self, job_id: int, *, limit: int = 100) -> tuple[dict[str, object], ...]:
        job_id = self._id(job_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise QueueError("limit must be an integer from 1 to 500")
        with self._lock:
            rows = self._db.execute(
                "SELECT sequence,job_id,event,at,worker,error_code FROM qa_audit WHERE job_id=? ORDER BY sequence LIMIT ?",
                (job_id, limit)).fetchall()
        return tuple(dict(row) for row in rows)

    def counts(self) -> dict[str, int]:
        result = {key: 0 for key in ("queued", "leased", "succeeded", "failed")}
        with self._lock:
            rows = self._db.execute("SELECT status,COUNT(*) AS n FROM qa_jobs GROUP BY status").fetchall()
        result.update({row["status"]: row["n"] for row in rows})
        return result
