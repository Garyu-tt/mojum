"""Optional, offline delegation ledger for Spartan leases (#701).

This is a bounded reference adapter contract, NOT a Ray/Temporal connection,
GitHub publisher, second scheduler, or authorization service. The only source
of task ownership is tools.spartan_coordinator's live SQLite lease row.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3

SCHEMA = "mojum.spartan-delegation/1"
MAX_ATTEMPTS = 3
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
ACTIVE = ("pending", "running")


class DelegationError(ValueError):
    """Fail closed on an invalid/stale delegated action."""


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise DelegationError(f"{name} must be an integer >= {minimum}")
    return value


def _digest(value, name):
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise DelegationError(f"{name} must be lowercase SHA-256 hex")
    return value


def _task_id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 120:
        raise DelegationError("invalid task id")
    return value


@dataclass(frozen=True)
class Ticket:
    """Opaque routing identity, not proof of authority or authentication."""
    task_id: str
    worker_id: int
    attempt: int
    dispatch_id: str
    lease_until: int

    def routing_packet(self, backend: str) -> dict:
        """Bounded external payload: no prompt, host, PRIVATE data or credentials."""
        if backend not in ("ray", "temporal", "fixture"):
            raise DelegationError("backend not allowlisted")
        return {"schema": SCHEMA, "backend": backend,
                "dispatch_id": self.dispatch_id, "attempt": self.attempt,
                "deadline_s": self.lease_until, "payload": "out-of-band-authorized"}


def _dispatch_id(task_id: str, worker_id: int, attempt: int) -> str:
    canonical = json.dumps([SCHEMA, task_id, worker_id, attempt],
                           ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_schema(db: sqlite3.Connection) -> None:
    """Opt-in coordinator-local side tables; no change to tasks or Noosphere."""
    db.executescript("""
    CREATE TABLE IF NOT EXISTS delegated_jobs (
        task_id TEXT NOT NULL,
        attempt INTEGER NOT NULL,
        worker_id INTEGER NOT NULL,
        dispatch_id TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL CHECK(state IN
            ('pending','running','reported','cancelled','orphaned')),
        result_sha256 TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY(task_id,attempt)
    );
    CREATE TABLE IF NOT EXISTS delegated_pr_intents (
        task_id TEXT PRIMARY KEY,
        dispatch_id TEXT NOT NULL,
        result_sha256 TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    """)


def _lease(db, task_id: str, worker_id: int, attempt: int, now: int):
    _task_id(task_id)
    _integer(worker_id, "worker_id", 1)
    if worker_id > 300:
        raise DelegationError("worker_id exceeds Spartan capacity")
    _integer(attempt, "attempt", 1)
    if attempt > MAX_ATTEMPTS:
        raise DelegationError("retry budget exceeded")
    _integer(now, "now")
    row = db.execute(
        "SELECT state, worker_id, attempts, lease_until FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    if (row is None or row["state"] != "running"
            or row["worker_id"] != worker_id
            or row["attempts"] != attempt
            or type(row["lease_until"]) is not int
            or row["lease_until"] <= now):
        raise DelegationError("current Spartan lease absent, stale or expired")
    return row


def _existing(db, task_id, attempt):
    return db.execute(
        "SELECT * FROM delegated_jobs WHERE task_id=? AND attempt=?",
        (task_id, attempt),
    ).fetchone()


def _ticket(task_id, worker_id, attempt, until):
    return Ticket(task_id, worker_id, attempt,
                  _dispatch_id(task_id, worker_id, attempt), until)


def submit(db: sqlite3.Connection, task_id: str, worker_id: int,
           attempt: int, now: int) -> Ticket:
    """Durable idempotent submit; never executes an external side effect."""
    db.execute("BEGIN IMMEDIATE")
    try:
        lease = _lease(db, task_id, worker_id, attempt, now)
        earlier = db.execute(
            "SELECT attempt, state FROM delegated_jobs WHERE task_id=? AND attempt<>?"
            " AND state IN ('pending','running')", (task_id, attempt),
        ).fetchall()
        # No live previous attempt can coexist. Reconcile after checking the
        # authoritative lease, so a crash/reclaim cannot leave ghost work.
        for old in earlier:
            db.execute(
                "UPDATE delegated_jobs SET state='orphaned', updated_at=?"
                " WHERE task_id=? AND attempt=?",
                (now, task_id, old["attempt"]),
            )
        ticket = _ticket(task_id, worker_id, attempt, lease["lease_until"])
        current = _existing(db, task_id, attempt)
        if current:
            if (current["worker_id"] != worker_id
                    or current["dispatch_id"] != ticket.dispatch_id
                    or current["state"] not in ACTIVE):
                raise DelegationError("cannot revive terminal delegation")
        else:
            db.execute("INSERT INTO delegated_jobs"
                       " (task_id,attempt,worker_id,dispatch_id,state,created_at,updated_at)"
                       " VALUES (?,?,?,?,'pending',?,?)",
                       (task_id, attempt, worker_id, ticket.dispatch_id, now, now))
        db.execute("COMMIT")
        return ticket
    except BaseException:
        db.execute("ROLLBACK")
        raise


def _change(db, ticket: Ticket, now: int, allowed: tuple[str, ...],
            target: str, result_sha256=None) -> bool:
    if not isinstance(ticket, Ticket):
        raise DelegationError("ticket required")
    if ticket.dispatch_id != _dispatch_id(ticket.task_id, ticket.worker_id, ticket.attempt):
        raise DelegationError("invalid dispatch identity")
    db.execute("BEGIN IMMEDIATE")
    try:
        lease = _lease(db, ticket.task_id, ticket.worker_id, ticket.attempt, now)
        # Renewals change the deadline, not authority. Caller MUST use
        # coordinator.heartbeat for renewal, not trust ticket.lease_until.
        if lease["lease_until"] < ticket.lease_until:
            raise DelegationError("lease deadline regressed")
        existing = _existing(db, ticket.task_id, ticket.attempt)
        if not existing or existing["dispatch_id"] != ticket.dispatch_id:
            raise DelegationError("unknown delegated attempt")
        if existing["state"] == target and existing["result_sha256"] == result_sha256:
            db.execute("COMMIT")
            return False
        if existing["state"] not in allowed:
            raise DelegationError("invalid delegated state transition")
        db.execute("UPDATE delegated_jobs SET state=?, result_sha256=?, updated_at=?"
                   " WHERE task_id=? AND attempt=?",
                   (target, result_sha256, now, ticket.task_id, ticket.attempt))
        db.execute("COMMIT")
        return True
    except BaseException:
        db.execute("ROLLBACK")
        raise


def start(db, ticket: Ticket, now: int) -> bool:
    """Mark delegated work started only under a live lease."""
    return _change(db, ticket, now, ("pending",), "running")


def cancel(db, ticket: Ticket, now: int) -> bool:
    """Revoke future result acceptance; backend cancellation is best-effort."""
    return _change(db, ticket, now, ACTIVE, "cancelled")


def report(db, ticket: Ticket, now: int, result_sha256: str) -> bool:
    """Accept artifact *digest*, not artifact content or GitHub publication."""
    _digest(result_sha256, "result_sha256")
    return _change(db, ticket, now, ("running",), "reported", result_sha256)


def reconcile(db: sqlite3.Connection, now: int) -> int:
    """Fenced orphan detection after host failures, expiry and lease reclaim."""
    _integer(now, "now")
    db.execute("BEGIN IMMEDIATE")
    try:
        updated = db.execute("""
            UPDATE delegated_jobs SET state='orphaned', updated_at=?
            WHERE state IN ('pending','running') AND NOT EXISTS (
                SELECT 1 FROM tasks AS t WHERE t.id=delegated_jobs.task_id
                  AND t.state='running' AND t.worker_id=delegated_jobs.worker_id
                  AND t.attempts=delegated_jobs.attempt AND t.lease_until>?
            )
        """, (now, now)).rowcount
        db.execute("COMMIT")
        return updated
    except BaseException:
        db.execute("ROLLBACK")
        raise


def prepare_pr_intent(db: sqlite3.Connection, ticket: Ticket,
                      now: int, result_sha256: str) -> bool:
    """One durable PREPARE per task; never publishes a PR itself.

    False means an intent already exists, including unknown remote outcomes
    after a crash. The caller must query GitHub by dispatch_id before any
    external action. This prevents automated blind duplicate publication but
    does not offer distributed exactly-once side effects.
    """
    _digest(result_sha256, "result_sha256")
    if not isinstance(ticket, Ticket) or ticket.dispatch_id != _dispatch_id(
            ticket.task_id, ticket.worker_id, ticket.attempt):
        raise DelegationError("invalid ticket")
    db.execute("BEGIN IMMEDIATE")
    try:
        _lease(db, ticket.task_id, ticket.worker_id, ticket.attempt, now)
        row = _existing(db, ticket.task_id, ticket.attempt)
        if not row or row["state"] != "reported" or row["result_sha256"] != result_sha256:
            raise DelegationError("unreported artifact or mismatched digest")
        prior = db.execute("SELECT 1 FROM delegated_pr_intents WHERE task_id=?",
                           (ticket.task_id,)).fetchone()
        if prior:
            db.execute("COMMIT")
            return False
        db.execute("INSERT INTO delegated_pr_intents"
                   " (task_id,dispatch_id,result_sha256,created_at) VALUES(?,?,?,?)",
                   (ticket.task_id, ticket.dispatch_id, result_sha256, now))
        db.execute("COMMIT")
        return True
    except BaseException:
        db.execute("ROLLBACK")
        raise


def states(db: sqlite3.Connection) -> dict:
    return {name: db.execute("SELECT COUNT(*) FROM delegated_jobs WHERE state=?",
                             (name,)).fetchone()[0]
            for name in ("pending", "running", "reported", "cancelled", "orphaned")}
