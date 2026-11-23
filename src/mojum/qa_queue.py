"""Durable, lease-based SQLite queue for independent QA supervisor workers.

This module is deliberately transport- and model-agnostic. It stores bounded
JSON payloads and scheduling metadata only; it does not authorize merges.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Mapping

SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 256 * 1024
ACTIVE_STATES = frozenset({"QUEUED", "CLAIMED", "BLOCKED", "COMPLETED"})


class QueueError(RuntimeError):
    """Base class for durable queue errors."""


class LeaseLost(QueueError):
    """The caller no longer owns the job lease."""


class InvalidJob(QueueError, ValueError):
    """A job key, payload, or lease argument is invalid."""


@dataclass(frozen=True)
class Job:
    id: int
    idempotency_key: str
    payload: Mapping[str, Any]
    status: str
    attempts: int
    lease_owner: str | None
    lease_until: float | None
    last_error: str | None


class DurableQueue:
    """A small SQLite WAL queue with atomic leases and restart recovery.

    A worker must heartbeat before lease expiry. Expired claims are returned
    to QUEUED inside the next claim transaction. BLOCKED jobs are deliberately
    excluded from selection, so one blocked PR cannot stop unrelated work.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("DurableQueue requires a file-backed SQLite database")
        self._clock = clock
        if not isinstance(busy_timeout_ms, int) or isinstance(busy_timeout_ms, bool) or busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be a non-negative integer")
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize(busy_timeout_ms)

    def _connect(self, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path, timeout=busy_timeout_ms / 1000, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self, busy_timeout_ms: int) -> None:
        with closing(self._connect(busy_timeout_ms)) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA synchronous = FULL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS qa_queue_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            row = db.execute(
                "SELECT value FROM qa_queue_meta WHERE key='schema_version'"
            ).fetchone()
            if row is not None and row["value"] != str(SCHEMA_VERSION):
                raise QueueError(
                    f"unsupported queue schema version {row['value']!r}"
                )
            db.execute(
                "INSERT OR IGNORE INTO qa_queue_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS qa_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('QUEUED','CLAIMED','BLOCKED','COMPLETED')),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                    lease_owner TEXT,
                    lease_until REAL,
                    heartbeat_at REAL,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    CHECK(
                        (status = 'CLAIMED' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL)
                        OR
                        (status != 'CLAIMED' AND lease_owner IS NULL AND lease_until IS NULL)
                    )
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS qa_jobs_ready ON qa_jobs(status, id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS qa_jobs_lease ON qa_jobs(status, lease_until)"
            )

    @staticmethod
    def _validate_key(key: str) -> str:
        if not isinstance(key, str) or not key.strip() or len(key) > 200:
            raise InvalidJob("idempotency_key must be 1..200 nonblank characters")
        return key.strip()

    @staticmethod
    def _serialize(payload: Mapping[str, Any]) -> str:
        if not isinstance(payload, Mapping):
            raise InvalidJob("payload must be a JSON object")
        try:
            encoded = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise InvalidJob(f"payload must contain finite JSON values: {exc}") from exc
        if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise InvalidJob(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
        return encoded

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            idempotency_key=row["idempotency_key"],
            payload=json.loads(row["payload_json"]),
            status=row["status"],
            attempts=row["attempts"],
            lease_owner=row["lease_owner"],
            lease_until=row["lease_until"],
            last_error=row["last_error"],
        )

    def enqueue(self, idempotency_key: str, payload: Mapping[str, Any]) -> Job:
        """Insert once; duplicate keys return the original job unchanged."""
        key = self._validate_key(idempotency_key)
        encoded = self._serialize(payload)
        now = float(self._clock())
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                INSERT OR IGNORE INTO qa_jobs
                    (idempotency_key, payload_json, status, created_at, updated_at)
                VALUES (?, ?, 'QUEUED', ?, ?)
                """,
                (key, encoded, now, now),
            )
            row = db.execute(
                "SELECT * FROM qa_jobs WHERE idempotency_key=?", (key,)
            ).fetchone()
            db.commit()
        return self._job(row)

    def claim(self, worker_id: str, *, lease_seconds: float = 60.0) -> Job | None:
        """Atomically claim the oldest ready job, reclaiming expired leases."""
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200:
            raise InvalidJob("worker_id must be 1..200 nonblank characters")
        if (isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float))
                or not math.isfinite(float(lease_seconds)) or lease_seconds <= 0):
            raise InvalidJob("lease_seconds must be positive")
        now = float(self._clock())
        until = now + float(lease_seconds)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                UPDATE qa_jobs
                SET status='QUEUED', lease_owner=NULL, lease_until=NULL,
                    heartbeat_at=NULL, updated_at=?
                WHERE status='CLAIMED' AND lease_until <= ?
                """,
                (now, now),
            )
            row = db.execute(
                "SELECT id FROM qa_jobs WHERE status='QUEUED' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                db.commit()
                return None
            db.execute(
                """
                UPDATE qa_jobs
                SET status='CLAIMED', lease_owner=?, lease_until=?,
                    heartbeat_at=?, attempts=attempts+1, updated_at=?
                WHERE id=? AND status='QUEUED'
                """,
                (worker_id.strip(), until, now, now, row["id"]),
            )
            claimed = db.execute(
                "SELECT * FROM qa_jobs WHERE id=?", (row["id"],)
            ).fetchone()
            db.commit()
        return self._job(claimed)

    def heartbeat(
        self, job_id: int, worker_id: str, *, lease_seconds: float = 60.0
    ) -> Job:
        """Extend a live lease; an expired or reassigned lease cannot revive."""
        if (isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float))
                or not math.isfinite(float(lease_seconds)) or lease_seconds <= 0):
            raise InvalidJob("lease_seconds must be positive")
        now = float(self._clock())
        until = now + float(lease_seconds)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """
                UPDATE qa_jobs
                SET lease_until=?, heartbeat_at=?, updated_at=?
                WHERE id=? AND status='CLAIMED' AND lease_owner=? AND lease_until > ?
                """,
                (until, now, now, job_id, worker_id, now),
            )
            if cursor.rowcount != 1:
                db.rollback()
                raise LeaseLost(f"worker {worker_id!r} no longer owns live job {job_id}")
            row = db.execute("SELECT * FROM qa_jobs WHERE id=?", (job_id,)).fetchone()
            db.commit()
        return self._job(row)

    def complete(self, job_id: int, worker_id: str) -> Job:
        return self._finish(job_id, worker_id, "COMPLETED", None)

    def block(self, job_id: int, worker_id: str, reason: str) -> Job:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise InvalidJob("reason must be 1..2000 nonblank characters")
        return self._finish(job_id, worker_id, "BLOCKED", reason.strip())

    def _finish(
        self, job_id: int, worker_id: str, status: str, reason: str | None
    ) -> Job:
        now = float(self._clock())
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """
                UPDATE qa_jobs
                SET status=?, lease_owner=NULL, lease_until=NULL,
                    heartbeat_at=NULL, last_error=?, updated_at=?
                WHERE id=? AND status='CLAIMED' AND lease_owner=? AND lease_until > ?
                """,
                (status, reason, now, job_id, worker_id, now),
            )
            if cursor.rowcount != 1:
                db.rollback()
                raise LeaseLost(f"worker {worker_id!r} no longer owns live job {job_id}")
            row = db.execute("SELECT * FROM qa_jobs WHERE id=?", (job_id,)).fetchone()
            db.commit()
        return self._job(row)

    def unblock(self, job_id: int) -> Job:
        """Return a manually-unblocked job to the ready queue."""
        now = float(self._clock())
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """
                UPDATE qa_jobs SET status='QUEUED', last_error=NULL, updated_at=?
                WHERE id=? AND status='BLOCKED'
                """,
                (now, job_id),
            )
            if cursor.rowcount != 1:
                db.rollback()
                raise QueueError(f"job {job_id} is not blocked")
            row = db.execute("SELECT * FROM qa_jobs WHERE id=?", (job_id,)).fetchone()
            db.commit()
        return self._job(row)

    def get(self, job_id: int) -> Job | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM qa_jobs WHERE id=?", (job_id,)).fetchone()
        return self._job(row) if row is not None else None

    def counts(self) -> dict[str, int]:
        """Return aggregate counts only; never expose job payloads."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS count FROM qa_jobs GROUP BY status"
            ).fetchall()
        result = {status: 0 for status in ACTIVE_STATES}
        result.update({row["status"]: row["count"] for row in rows})
        return result
