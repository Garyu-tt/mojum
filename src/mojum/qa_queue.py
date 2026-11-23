"""Durable, process-safe SQLite work queue for the Mojum QA supervisor.

The queue stores task metadata and caller-supplied JSON payloads only. It does
not contact GitHub, execute workers, or grant merge authority.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import json
import math
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any


class QueueError(Exception):
    """Base class for queue contract violations."""


class LeaseLostError(QueueError):
    """The caller no longer owns the task lease."""


class DuplicateTaskError(QueueError):
    """An idempotency key was reused with a different task identity."""


@dataclass(frozen=True, slots=True)
class Task:
    task_id: int
    task_key: str
    idempotency_key: str
    payload: dict[str, Any]
    state: str
    attempts: int
    available_at_ns: int
    lease_owner: str | None
    lease_token: str | None
    lease_until_ns: int | None


@dataclass(frozen=True, slots=True)
class QueueEvent:
    event_id: int
    task_id: int
    event: str
    worker_id: str | None
    at_ns: int
    detail: str | None


def _text(value: object, name: str, *, maximum: int = 256) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a non-empty string of at most {maximum} characters")
    if value.strip() != value or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{name} contains surrounding whitespace or control characters")
    return value


def _positive_int(value: object, name: str, *, maximum: int) -> int:
    if type(value) is not int or value <= 0 or value > maximum:
        raise ValueError(f"{name} must be an integer in [1, {maximum}]")
    return value


def _timestamp(value: object) -> int:
    if value is None:
        return time.time_ns()
    if type(value) is not int or value < 0 or value > 9_223_372_036_854_775_807:
        raise ValueError("now_ns must be a non-negative signed 64-bit integer")
    return value


def _validate_json_value(value: object, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("payload nesting exceeds 32 levels")
    if value is None or type(value) is bool:
        return
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("payload strings must be valid UTF-8") from exc
        return
    if type(value) is int:
        if value < -9_223_372_036_854_775_808 or value > 9_223_372_036_854_775_807:
            raise ValueError("payload integers must fit signed 64-bit range")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("payload floats must be finite")
        return
    if type(value) is list:
        for item in value:
            _validate_json_value(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("payload object keys must be plain strings")
            _validate_json_value(key, depth + 1)
            _validate_json_value(item, depth + 1)
        return
    raise ValueError("payload must contain only plain JSON values")


class DurableTaskQueue:
    """A small SQLite queue with atomic claims and expiring ownership leases.

    Each public operation opens its own connection. SQLite BEGIN IMMEDIATE
    serializes claim/transition decisions across independent processes.
    """

    def __init__(
        self,
        database: str | Path,
        *,
        timeout_seconds: float = 10.0,
        max_payload_bytes: int = 65_536,
    ) -> None:
        if not isinstance(database, (str, Path)) or not str(database):
            raise ValueError("database must be a non-empty file-backed path")
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        self.database = str(database)
        if self.database == ":memory:":
            raise ValueError("file-backed SQLite is required for independent worker connections")
        self.timeout_seconds = float(timeout_seconds)
        self.max_payload_bytes = _positive_int(max_payload_bytes, "max_payload_bytes", maximum=1_048_576)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database,
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            # Use WAL for durable file-backed worker coordination.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS queue_tasks (
                    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_key TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('queued','leased','completed','failed')),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                    available_at_ns INTEGER NOT NULL CHECK (available_at_ns >= 0),
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_until_ns INTEGER,
                    created_at_ns INTEGER NOT NULL,
                    updated_at_ns INTEGER NOT NULL,
                    CHECK (
                        (state = 'leased' AND lease_owner IS NOT NULL
                         AND lease_token IS NOT NULL AND lease_until_ns IS NOT NULL)
                        OR
                        (state <> 'leased' AND lease_owner IS NULL
                         AND lease_token IS NULL AND lease_until_ns IS NULL)
                    )
                );
                CREATE INDEX IF NOT EXISTS queue_tasks_claim_idx
                    ON queue_tasks(state, available_at_ns, lease_until_ns, task_id);
                CREATE TABLE IF NOT EXISTS queue_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES queue_tasks(task_id),
                    event TEXT NOT NULL,
                    worker_id TEXT,
                    at_ns INTEGER NOT NULL,
                    detail TEXT
                );
                CREATE INDEX IF NOT EXISTS queue_events_task_idx
                    ON queue_events(task_id, event_id);
                """
            )

    def _payload_json(self, payload: object) -> str:
        if type(payload) is not dict:
            raise ValueError("payload must be a plain dict")
        _validate_json_value(payload)
        try:
            encoded = json.dumps(
                payload, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
        except (TypeError, ValueError, RecursionError, UnicodeEncodeError) as exc:
            raise ValueError("payload must contain only finite JSON values") from exc
        if len(encoded.encode("utf-8")) > self.max_payload_bytes:
            raise ValueError("payload exceeds max_payload_bytes")
        return encoded

    @staticmethod
    def _task(row: sqlite3.Row) -> Task:
        return Task(
            task_id=row["task_id"],
            task_key=row["task_key"],
            idempotency_key=row["idempotency_key"],
            payload=json.loads(row["payload_json"]),
            state=row["state"],
            attempts=row["attempts"],
            available_at_ns=row["available_at_ns"],
            lease_owner=row["lease_owner"],
            lease_token=row["lease_token"],
            lease_until_ns=row["lease_until_ns"],
        )

    @staticmethod
    def _event(connection: sqlite3.Connection, task_id: int, event: str,
               worker_id: str | None, at_ns: int, detail: str | None = None) -> None:
        connection.execute(
            "INSERT INTO queue_events(task_id,event,worker_id,at_ns,detail) VALUES(?,?,?,?,?)",
            (task_id, event, worker_id, at_ns, detail),
        )

    def enqueue(
        self,
        task_key: str,
        idempotency_key: str,
        payload: dict[str, Any],
        *,
        available_at_ns: int | None = None,
        now_ns: int | None = None,
    ) -> Task:
        task_key = _text(task_key, "task_key")
        idempotency_key = _text(idempotency_key, "idempotency_key")
        payload_json = self._payload_json(payload)
        now = _timestamp(now_ns)
        available = now if available_at_ns is None else _timestamp(available_at_ns)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM queue_tasks WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if (existing["task_key"] != task_key or
                        existing["payload_json"] != payload_json):
                    raise DuplicateTaskError("idempotency key already belongs to a different task")
                connection.commit()
                return self._task(existing)
            cursor = connection.execute(
                """INSERT INTO queue_tasks
                   (task_key,idempotency_key,payload_json,state,available_at_ns,
                    created_at_ns,updated_at_ns)
                   VALUES(?,?,?,'queued',?,?,?)""",
                (task_key, idempotency_key, payload_json, available, now, now),
            )
            task_id = int(cursor.lastrowid)
            self._event(connection, task_id, "enqueued", None, now)
            row = connection.execute(
                "SELECT * FROM queue_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            connection.commit()
            return self._task(row)

    def claim(self, worker_id: str, *, lease_seconds: int = 60,
              now_ns: int | None = None) -> Task | None:
        worker_id = _text(worker_id, "worker_id")
        lease_seconds = _positive_int(lease_seconds, "lease_seconds", maximum=86_400)
        now = _timestamp(now_ns)
        lease_until = now + lease_seconds * 1_000_000_000
        if lease_until > 9_223_372_036_854_775_807:
            raise ValueError("lease expiry exceeds signed 64-bit timestamp range")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM queue_tasks
                   WHERE (state='queued' AND available_at_ns <= ?)
                      OR (state='leased' AND lease_until_ns <= ?)
                   ORDER BY available_at_ns, task_id LIMIT 1""",
                (now, now),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            token = secrets.token_urlsafe(24)
            if row["state"] == "leased":
                self._event(connection, row["task_id"], "lease_expired",
                            row["lease_owner"], now)
            connection.execute(
                """UPDATE queue_tasks SET state='leased', attempts=attempts+1,
                   lease_owner=?, lease_token=?, lease_until_ns=?, updated_at_ns=?
                   WHERE task_id=?""",
                (worker_id, token, lease_until, now, row["task_id"]),
            )
            self._event(connection, row["task_id"], "claimed", worker_id, now)
            claimed = connection.execute(
                "SELECT * FROM queue_tasks WHERE task_id=?", (row["task_id"],)
            ).fetchone()
            connection.commit()
            return self._task(claimed)

    def heartbeat(self, task_id: int, worker_id: str, lease_token: str, *,
                  lease_seconds: int = 60, now_ns: int | None = None) -> Task:
        return self._renew(task_id, worker_id, lease_token, lease_seconds, now_ns, "heartbeat")

    def _renew(self, task_id: int, worker_id: str, lease_token: str,
               lease_seconds: int, now_ns: int | None, event: str) -> Task:
        task_id = _positive_int(task_id, "task_id", maximum=9_223_372_036_854_775_807)
        worker_id = _text(worker_id, "worker_id")
        lease_token = _text(lease_token, "lease_token", maximum=128)
        lease_seconds = _positive_int(lease_seconds, "lease_seconds", maximum=86_400)
        now = _timestamp(now_ns)
        until = now + lease_seconds * 1_000_000_000
        if until > 9_223_372_036_854_775_807:
            raise ValueError("lease expiry exceeds signed 64-bit timestamp range")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM queue_tasks WHERE task_id=? AND state='leased'
                   AND lease_owner=? AND lease_token=? AND lease_until_ns > ?""",
                (task_id, worker_id, lease_token, now),
            ).fetchone()
            if row is None:
                raise LeaseLostError("task lease is missing, expired, or owned by another worker")
            connection.execute(
                "UPDATE queue_tasks SET lease_until_ns=MAX(lease_until_ns,?),updated_at_ns=? WHERE task_id=?",
                (until, now, task_id),
            )
            self._event(connection, task_id, event, worker_id, now)
            updated = connection.execute(
                "SELECT * FROM queue_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            connection.commit()
            return self._task(updated)

    def complete(self, task_id: int, worker_id: str, lease_token: str, *,
                 now_ns: int | None = None) -> Task:
        return self._finish(task_id, worker_id, lease_token, "completed", now_ns)

    def fail(self, task_id: int, worker_id: str, lease_token: str, *,
             retry: bool = False, available_at_ns: int | None = None,
             now_ns: int | None = None) -> Task:
        if type(retry) is not bool:
            raise ValueError("retry must be a bool")
        return self._finish(
            task_id, worker_id, lease_token, "queued" if retry else "failed",
            now_ns, available_at_ns=available_at_ns,
        )

    def _finish(self, task_id: int, worker_id: str, lease_token: str,
                state: str, now_ns: int | None,
                available_at_ns: int | None = None) -> Task:
        task_id = _positive_int(task_id, "task_id", maximum=9_223_372_036_854_775_807)
        worker_id = _text(worker_id, "worker_id")
        lease_token = _text(lease_token, "lease_token", maximum=128)
        now = _timestamp(now_ns)
        available = now if available_at_ns is None else _timestamp(available_at_ns)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM queue_tasks WHERE task_id=? AND state='leased'
                   AND lease_owner=? AND lease_token=? AND lease_until_ns > ?""",
                (task_id, worker_id, lease_token, now),
            ).fetchone()
            if row is None:
                raise LeaseLostError("task lease is missing, expired, or owned by another worker")
            connection.execute(
                """UPDATE queue_tasks SET state=?,available_at_ns=?,lease_owner=NULL,
                   lease_token=NULL,lease_until_ns=NULL,updated_at_ns=? WHERE task_id=?""",
                (state, available, now, task_id),
            )
            self._event(connection, task_id, state, worker_id, now)
            updated = connection.execute(
                "SELECT * FROM queue_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            connection.commit()
            return self._task(updated)

    def get(self, task_id: int) -> Task | None:
        task_id = _positive_int(task_id, "task_id", maximum=9_223_372_036_854_775_807)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM queue_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            return None if row is None else self._task(row)

    def events(self, task_id: int, *, limit: int = 100) -> tuple[QueueEvent, ...]:
        task_id = _positive_int(task_id, "task_id", maximum=9_223_372_036_854_775_807)
        limit = _positive_int(limit, "limit", maximum=1_000)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT event_id,task_id,event,worker_id,at_ns,detail
                   FROM queue_events WHERE task_id=? ORDER BY event_id LIMIT ?""",
                (task_id, limit),
            ).fetchall()
        return tuple(QueueEvent(**dict(row)) for row in rows)
