"""Process-safe durable QA queue. Scheduling metadata only; never merge authority."""
from __future__ import annotations
from dataclasses import dataclass
import json, os, sqlite3, time, uuid
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "mojum.qa-supervisor-queue/1"
MAX_PAYLOAD_BYTES = 65536
ACTIVE = ("claimed", "reviewing", "testing", "fixing", "retesting")
STATES = frozenset(("discovered", "claimed", "reviewing", "testing", "fixing",
    "retesting", "ready", "merged", "waiting_ci", "blocked_permission",
    "blocked_conflict", "escalated"))
TRANSITIONS = {
 "discovered": {"claimed","waiting_ci","blocked_permission","blocked_conflict","escalated"},
 "claimed": {"reviewing","testing","fixing","retesting","ready","waiting_ci","blocked_permission","blocked_conflict","escalated","discovered"},
 "reviewing": {"testing","fixing","retesting","ready","waiting_ci","blocked_permission","blocked_conflict","escalated","discovered"},
 "testing": {"fixing","retesting","ready","waiting_ci","blocked_permission","blocked_conflict","escalated","discovered"},
 "fixing": {"retesting","testing","waiting_ci","blocked_permission","blocked_conflict","escalated","discovered"},
 "retesting": {"fixing","ready","waiting_ci","blocked_permission","blocked_conflict","escalated","discovered"},
 "ready": {"discovered","merged","blocked_conflict","escalated"},
 "waiting_ci": {"discovered","blocked_permission","blocked_conflict","escalated"},
 "blocked_permission": {"discovered","escalated"},
 "blocked_conflict": {"discovered","escalated"},
 "escalated": {"discovered","blocked_permission","blocked_conflict"},
 "merged": set(),
}

class QueueError(RuntimeError): pass
class LeaseError(QueueError): pass
class TransitionError(QueueError): pass

@dataclass(frozen=True)
class QueueJob:
    job_id: str
    issue_number: int
    head_sha: str
    idempotency_key: str
    state: str
    payload: Mapping[str, Any]
    worker_id: str | None
    lease_token: str | None
    lease_until: float | None
    attempts: int
    created_at: float
    updated_at: float
    last_error: str | None

class DurableQAQueue:
    def __init__(self, database: str | os.PathLike[str], *, busy_timeout_ms: int = 5000):
        self.database = str(database)
        if not self.database or self.database == ":memory:":
            raise ValueError("a durable filesystem database path is required")
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 120000:
            raise ValueError("invalid busy_timeout_ms")
        Path(self.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = busy_timeout_ms
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS qa_jobs (
              job_id TEXT PRIMARY KEY, issue_number INTEGER NOT NULL CHECK(issue_number>0),
              head_sha TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
              payload_json TEXT NOT NULL, state TEXT NOT NULL, worker_id TEXT,
              lease_token TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
              created_at REAL NOT NULL, updated_at REAL NOT NULL, last_error TEXT,
              CHECK ((lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL)
                OR (lease_token IS NOT NULL AND lease_until IS NOT NULL AND worker_id IS NOT NULL))
            );
            CREATE INDEX IF NOT EXISTS qa_jobs_state_idx ON qa_jobs(state,created_at,job_id);
            CREATE INDEX IF NOT EXISTS qa_jobs_lease_idx ON qa_jobs(state,lease_until);
            """)

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=self.busy_timeout_ms/1000, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        return db

    @staticmethod
    def _decode(row):
        if row is None: return None
        return QueueJob(row["job_id"], row["issue_number"], row["head_sha"],
            row["idempotency_key"], row["state"], json.loads(row["payload_json"]),
            row["worker_id"], row["lease_token"], row["lease_until"], row["attempts"],
            row["created_at"], row["updated_at"], row["last_error"])

    @staticmethod
    def _identity(issue_number, head_sha):
        if type(issue_number) is not int or issue_number <= 0:
            raise ValueError("issue_number must be a positive native integer")
        if type(head_sha) is not str or len(head_sha) not in (40,64) or any(c not in "0123456789abcdefABCDEF" for c in head_sha):
            raise ValueError("head_sha must be a full hexadecimal Git object ID")

    @staticmethod
    def _time(now):
        value = time.time() if now is None else now
        if type(value) not in (int,float) or value < 0 or value == float("inf") or value != value:
            raise ValueError("timestamp must be finite and nonnegative")
        return float(value)

    def enqueue(self, issue_number: int, head_sha: str, payload: Mapping[str,Any] | None = None,
                *, idempotency_key: str | None = None, now: float | None = None) -> QueueJob:
        self._identity(issue_number,head_sha)
        payload = {} if payload is None else payload
        if type(payload) is not dict: raise ValueError("payload must be a plain dict")
        try: encoded=json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False)
        except (TypeError,ValueError) as exc: raise ValueError("payload must be finite JSON") from exc
        if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES: raise ValueError("payload exceeds 64 KiB")
        key=idempotency_key or f"{issue_number}:{head_sha.lower()}"
        if type(key) is not str or not key or len(key)>256: raise ValueError("invalid idempotency_key")
        ts=self._time(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT OR IGNORE INTO qa_jobs
              (job_id,issue_number,head_sha,idempotency_key,payload_json,state,attempts,created_at,updated_at)
              VALUES (?,?,?,?,?,'discovered',0,?,?)""",
              (uuid.uuid4().hex,issue_number,head_sha.lower(),key,encoded,ts,ts))
            row=db.execute("SELECT * FROM qa_jobs WHERE idempotency_key=?",(key,)).fetchone()
            db.commit()
        return self._decode(row)

    def claim(self, worker_id: str, *, lease_seconds: int=60, now: float | None=None) -> QueueJob | None:
        if type(worker_id) is not str or not worker_id.strip() or len(worker_id)>128: raise ValueError("invalid worker_id")
        if type(lease_seconds) is not int or not 1<=lease_seconds<=86400: raise ValueError("invalid lease_seconds")
        ts=self._time(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE qa_jobs SET state='discovered',worker_id=NULL,lease_token=NULL,
              lease_until=NULL,updated_at=? WHERE state IN ('claimed','reviewing','testing','fixing','retesting')
              AND lease_until<=?""",(ts,ts))
            row=db.execute("SELECT job_id FROM qa_jobs WHERE state='discovered' ORDER BY created_at,job_id LIMIT 1").fetchone()
            if row is None: db.commit(); return None
            token=uuid.uuid4().hex
            db.execute("""UPDATE qa_jobs SET state='claimed',worker_id=?,lease_token=?,lease_until=?,
              attempts=attempts+1,updated_at=? WHERE job_id=?""",
              (worker_id,token,ts+lease_seconds,ts,row["job_id"]))
            result=db.execute("SELECT * FROM qa_jobs WHERE job_id=?",(row["job_id"],)).fetchone()
            db.commit()
        return self._decode(result)

    def heartbeat(self, job_id: str, lease_token: str, *, lease_seconds: int=60,
                  now: float | None=None) -> QueueJob:
        if type(lease_seconds) is not int or not 1<=lease_seconds<=86400: raise ValueError("invalid lease_seconds")
        ts=self._time(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed=db.execute("""UPDATE qa_jobs SET lease_until=?,updated_at=? WHERE job_id=?
              AND lease_token=? AND state IN ('claimed','reviewing','testing','fixing','retesting')
              AND lease_until>?""",(ts+lease_seconds,ts,job_id,lease_token,ts)).rowcount
            if changed!=1: db.rollback(); raise LeaseError("lease missing, expired, or not owned")
            row=db.execute("SELECT * FROM qa_jobs WHERE job_id=?",(job_id,)).fetchone()
            db.commit()
        return self._decode(row)

    def transition(self, job_id: str, lease_token: str, new_state: str, *,
                   now: float | None=None, error: str | None=None) -> QueueJob:
        if new_state not in STATES: raise TransitionError(f"unknown state: {new_state!r}")
        if error is not None and (type(error) is not str or len(error)>2048): raise ValueError("invalid error")
        ts=self._time(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM qa_jobs WHERE job_id=?",(job_id,)).fetchone()
            if row is None or row["lease_token"]!=lease_token or row["lease_until"] is None or row["lease_until"]<=ts:
                db.rollback(); raise LeaseError("lease missing, expired, or not owned")
            if new_state not in TRANSITIONS[row["state"]]:
                db.rollback(); raise TransitionError(f"transition {row['state']!r} -> {new_state!r} is not allowed")
            if new_state in ACTIVE:
                db.execute("UPDATE qa_jobs SET state=?,updated_at=?,last_error=? WHERE job_id=?",
                           (new_state,ts,error,job_id))
            else:
                db.execute("""UPDATE qa_jobs SET state=?,worker_id=NULL,lease_token=NULL,
                  lease_until=NULL,updated_at=?,last_error=? WHERE job_id=?""",(new_state,ts,error,job_id))
            result=db.execute("SELECT * FROM qa_jobs WHERE job_id=?",(job_id,)).fetchone()
            db.commit()
        return self._decode(result)

    def get(self, job_id: str) -> QueueJob | None:
        with self._connect() as db: row=db.execute("SELECT * FROM qa_jobs WHERE job_id=?",(job_id,)).fetchone()
        return self._decode(row)

    def counts(self) -> dict[str,int]:
        with self._connect() as db: rows=db.execute("SELECT state,COUNT(*) n FROM qa_jobs GROUP BY state").fetchall()
        result={state:0 for state in sorted(STATES)}
        result.update({row["state"]:row["n"] for row in rows})
        return result
