"""Small, durable SQLite work queue for autonomous QA workers.

This module owns scheduling only. It does not poll GitHub, inspect code, approve
reviews, or authorize merges. All state changes are transactional and lease-token
guarded so stale workers cannot complete work after it has been reclaimed.
"""

from __future__ import annotations

from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable
import uuid


MAX_LEASE_SECONDS = 24 * 60 * 60
MAX_RETRY_DELAY_SECONDS = 7 * 24 * 60 * 60
MAX_PAYLOAD_BYTES = 1024 * 1024


class QueueError(ValueError):
    """Invalid queue operation or payload."""


class IdempotencyConflict(QueueError):
    """An idempotency key was reused for a different task."""


class LeaseLost(QueueError):
    """The lease expired, was reclaimed, or belongs to another worker."""


class DurableWorkQueue:
    """SQLite WAL queue with atomic claims and replay-safe enqueue.

    Use one instance per process if convenient; each operation opens its own
    connection, so threads and independent processes coordinate through SQLite.
    The database stores only caller-supplied task payloads. Do not put secrets or
    PRIVATE organism state in those payloads.
    """

    def __init__(self, database: str | Path, *, clock: Callable[[], float] = time.time):
        self.database = str(database)
        if self.database == ":memory:":
            raise QueueError("durable queue requires a filesystem-backed SQLite database")
        self._clock = clock
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS qa_tasks (
                    task_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('ready','leased','done','dead')),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                    max_attempts INTEGER NOT NULL CHECK(max_attempts > 0),
                    available_at REAL NOT NULL,
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_until REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_error TEXT,
                    CHECK (
                        (status = 'leased' AND lease_owner IS NOT NULL
                         AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
                        OR
                        (status != 'leased' AND lease_owner IS NULL
                         AND lease_token IS NULL AND lease_until IS NULL)
                    )
                );
                CREATE INDEX IF NOT EXISTS qa_tasks_ready_idx
                    ON qa_tasks(status, available_at, created_at);
                CREATE INDEX IF NOT EXISTS qa_tasks_lease_idx
                    ON qa_tasks(status, lease_until);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database, timeout=15.0, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _positive_seconds(value: float, name: str, maximum: int) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise QueueError(f"{name} must be a finite positive number")
        result = float(value)
        if not math.isfinite(result) or result <= 0 or result > maximum:
            raise QueueError(f"{name} must be in (0, {maximum}]")
        return result

    @staticmethod
    def _task(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def enqueue(
        self,
        task_id: str,
        idempotency_key: str,
        payload: Any,
        *,
        max_attempts: int = 5,
        available_at: float | None = None,
    ) -> dict[str, Any]:
        """Insert once; identical retries return the original task unchanged."""
        for value, label in ((task_id, "task_id"), (idempotency_key, "idempotency_key")):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise QueueError(f"{label} must be a nonempty string of at most 256 chars")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 1000:
            raise QueueError("max_attempts must be an integer in [1, 1000]")
        try:
            payload_json = json.dumps(
                payload, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise QueueError("payload must be finite JSON data") from exc
        if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise QueueError("payload exceeds 1 MiB")
        now = float(self._clock())
        due = now if available_at is None else float(available_at)
        if not math.isfinite(now) or not math.isfinite(due):
            raise QueueError("queue timestamps must be finite")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM qa_tasks WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if (existing["payload_json"] != payload_json
                        or existing["task_id"] != task_id
                        or existing["max_attempts"] != max_attempts):
                    connection.rollback()
                    raise IdempotencyConflict("idempotency key already belongs to different task parameters")
                connection.commit()
                return self._task(existing)
            try:
                connection.execute(
                    """INSERT INTO qa_tasks
                       (task_id,idempotency_key,payload_json,status,attempts,max_attempts,
                        available_at,created_at,updated_at)
                       VALUES (?,?,?,'ready',0,?,?,?,?)""",
                    (task_id, idempotency_key, payload_json, max_attempts, due, now, now),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise IdempotencyConflict("task_id or idempotency_key already exists") from exc
            row = connection.execute(
                "SELECT * FROM qa_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            connection.commit()
            return self._task(row)

    def claim(self, worker: str, *, lease_seconds: float = 60.0) -> dict[str, Any] | None:
        """Atomically claim the oldest due task, reclaiming expired leases."""
        if not isinstance(worker, str) or not worker.strip() or len(worker) > 256:
            raise QueueError("worker must be a nonempty string of at most 256 chars")
        ttl = self._positive_seconds(lease_seconds, "lease_seconds", MAX_LEASE_SECONDS)
        now = float(self._clock())
        if not math.isfinite(now):
            raise QueueError("clock must return a finite timestamp")
        token = uuid.uuid4().hex
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            # Exhausted tasks are terminal after a worker crashes repeatedly.
            connection.execute(
                """UPDATE qa_tasks
                   SET status='dead', lease_owner=NULL, lease_token=NULL, lease_until=NULL,
                       updated_at=?, last_error=COALESCE(last_error,'lease expired; attempts exhausted')
                   WHERE status='leased' AND lease_until <= ? AND attempts >= max_attempts""",
                (now, now),
            )
            row = connection.execute(
                """SELECT task_id FROM qa_tasks
                   WHERE attempts < max_attempts AND
                     ((status='ready' AND available_at <= ?)
                      OR (status='leased' AND lease_until <= ?))
                   ORDER BY available_at ASC, created_at ASC, task_id ASC LIMIT 1""",
                (now, now),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            task_id = row["task_id"]
            connection.execute(
                """UPDATE qa_tasks SET status='leased', attempts=attempts+1,
                       lease_owner=?, lease_token=?, lease_until=?, updated_at=?
                   WHERE task_id=?""",
                (worker, token, now + ttl, now, task_id),
            )
            claimed = connection.execute(
                "SELECT * FROM qa_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            connection.commit()
            return self._task(claimed)

    def _owned_lease(self, connection: sqlite3.Connection, task_id: str,
                     lease_token: str, now: float) -> sqlite3.Row:
        row = connection.execute(
            """SELECT * FROM qa_tasks WHERE task_id=? AND status='leased'
               AND lease_token=? AND lease_until > ?""",
            (task_id, lease_token, now),
        ).fetchone()
        if row is None:
            raise LeaseLost("lease is expired, reclaimed, or not owned by this worker")
        return row

    def heartbeat(self, task_id: str, lease_token: str, *,
                  lease_seconds: float = 60.0) -> float:
        """Extend a still-live lease. Expired leases cannot be resurrected."""
        ttl = self._positive_seconds(lease_seconds, "lease_seconds", MAX_LEASE_SECONDS)
        now = float(self._clock())
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_lease(connection, task_id, lease_token, now)
            until = now + ttl
            connection.execute(
                "UPDATE qa_tasks SET lease_until=?, updated_at=? WHERE task_id=?",
                (until, now, task_id),
            )
            connection.commit()
            return until

    def complete(self, task_id: str, lease_token: str) -> dict[str, Any]:
        """Mark done only when the caller still owns an unexpired lease."""
        now = float(self._clock())
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_lease(connection, task_id, lease_token, now)
            connection.execute(
                """UPDATE qa_tasks SET status='done', lease_owner=NULL, lease_token=NULL,
                       lease_until=NULL, updated_at=? WHERE task_id=?""",
                (now, task_id),
            )
            row = connection.execute("SELECT * FROM qa_tasks WHERE task_id=?", (task_id,)).fetchone()
            connection.commit()
            return self._task(row)

    def retry(self, task_id: str, lease_token: str, *,
              delay_seconds: float = 0.0, error: str | None = None) -> dict[str, Any]:
        """Release for retry, or move to dead when the attempt budget is spent."""
        if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, (int, float)):
            raise QueueError("delay_seconds must be finite and nonnegative")
        delay = float(delay_seconds)
        if not math.isfinite(delay) or delay < 0 or delay > MAX_RETRY_DELAY_SECONDS:
            raise QueueError(f"delay_seconds must be in [0, {MAX_RETRY_DELAY_SECONDS}]")
        if error is not None and (not isinstance(error, str) or len(error) > 4096):
            raise QueueError("error must be a string of at most 4096 chars")
        now = float(self._clock())
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._owned_lease(connection, task_id, lease_token, now)
            exhausted = current["attempts"] >= current["max_attempts"]
            status = "dead" if exhausted else "ready"
            connection.execute(
                """UPDATE qa_tasks SET status=?, available_at=?, lease_owner=NULL,
                       lease_token=NULL, lease_until=NULL, updated_at=?, last_error=?
                   WHERE task_id=?""",
                (status, now + delay, now, error, task_id),
            )
            row = connection.execute("SELECT * FROM qa_tasks WHERE task_id=?", (task_id,)).fetchone()
            connection.commit()
            return self._task(row)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            return self._task(connection.execute(
                "SELECT * FROM qa_tasks WHERE task_id=?", (task_id,)
            ).fetchone())

    def counts(self) -> dict[str, int]:
        """Return stable state counts for a read-only dashboard."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS amount FROM qa_tasks GROUP BY status"
            ).fetchall()
        return {row["status"]: row["amount"] for row in rows}
