"""Optional Ray/Temporal executor boundary under the Cezar/Spartan SQLite lease authority.

Issue #701. No implicit dependencies, network, remote process or PR publication.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

SCHEMA = "mojum.spartan.delegation/1"
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,119}\Z", re.ASCII)
_PR = re.compile(r"https://github\.com/Garyu-tt/mojum/pull/[1-9][0-9]{0,9}\Z", re.ASCII)


class DelegationRejected(ValueError):
    """Unsafe boundary, invalid state or an expired/fenced attempt."""


def _name(value, field):
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise DelegationRejected("invalid " + field)
    return value


def _positive(value, field):
    if type(value) is not int or not 1 <= value <= 2**31 - 1:
        raise DelegationRejected("invalid " + field)
    return value


def _clock(now):
    if type(now) is not int or not 0 <= now <= 2**63 - 1:
        raise DelegationRejected("invalid clock")


@dataclass(frozen=True)
class DelegatedLease:
    task_id: str
    worker_id: int
    attempt: int
    backend: str
    opaque_ref: str

    def __post_init__(self):
        _name(self.task_id, "task id")
        _positive(self.worker_id, "worker id")
        _positive(self.attempt, "attempt")
        if type(self.backend) is not str or self.backend not in ("ray", "temporal"):
            raise DelegationRejected("unsupported backend")
        _name(self.opaque_ref, "opaque ref")

    def wire(self):
        return {"schema": SCHEMA, "task_id": self.task_id,
                "worker_id": self.worker_id, "attempt": self.attempt,
                "backend": self.backend, "opaque_ref": self.opaque_ref}


def encode_wire(lease):
    if type(lease) is not DelegatedLease:
        raise DelegationRejected("lease object required")
    return json.dumps(lease.wire(), sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("ascii")


def validate_wire(blob):
    if type(blob) is not bytes or len(blob) > 1024:
        raise DelegationRejected("oversized or non-bytes packet")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DelegationRejected("duplicate packet key")
            result[key] = value
        return result

    def reject_constant(_):
        raise DelegationRejected("nonfinite JSON constant")

    try:
        data = json.loads(blob, object_pairs_hook=unique,
                          parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DelegationRejected("invalid packet") from exc
    if (type(data) is not dict or set(data) !=
            {"schema", "task_id", "worker_id", "attempt", "backend", "opaque_ref"}
            or data["schema"] != SCHEMA):
        raise DelegationRejected("unknown delegated schema")
    return DelegatedLease(data["task_id"], data["worker_id"], data["attempt"],
                          data["backend"], data["opaque_ref"])


def install(db):
    """Explicitly augment the trusted existing Spartan ledger; never a new authority."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='tasks' AND type='table'").fetchone() is None:
        raise DelegationRejected("coordinator schema required")
    # execute() preserves the caller's transaction; executescript() commits it.
    db.execute("""
        CREATE TABLE IF NOT EXISTS delegated_runs (
          task_id TEXT NOT NULL, attempt INTEGER NOT NULL, worker_id INTEGER NOT NULL,
          backend TEXT NOT NULL CHECK(backend IN ('ray','temporal')),
          opaque_ref TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('reserved','submitted','cancel_requested','orphaned')),
          external_id TEXT, PRIMARY KEY(task_id,attempt))
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS delegated_pr_intents (
          task_id TEXT PRIMARY KEY, attempt INTEGER NOT NULL, worker_id INTEGER NOT NULL,
          branch TEXT NOT NULL, head_sha TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('prepared','confirmed')), pr_url TEXT)
    """)


def _live(db, lease, now):
    return db.execute(
        "SELECT 1 FROM tasks WHERE id=? AND worker_id=? AND attempts=? "
        "AND state='running' AND lease_until>?",
        (lease.task_id, lease.worker_id, lease.attempt, now)
    ).fetchone() is not None


def _atomic(db, fn):
    db.execute("BEGIN IMMEDIATE")
    try:
        result = fn()
        db.execute("COMMIT")
        return result
    except BaseException:
        db.execute("ROLLBACK")
        raise


def reserve(db, lease, *, now):
    """True authorizes ONE remote dispatch; False means reconcile, never resend."""
    _clock(now)
    if type(lease) is not DelegatedLease:
        raise DelegationRejected("lease object required")

    def work():
        if not _live(db, lease, now):
            raise DelegationRejected("stale or expired coordinator lease")
        previous = db.execute(
            "SELECT worker_id,backend,opaque_ref FROM delegated_runs "
            "WHERE task_id=? AND attempt=?",
            (lease.task_id, lease.attempt)).fetchone()
        if previous:
            if tuple(previous) != (lease.worker_id, lease.backend, lease.opaque_ref):
                raise DelegationRejected("conflicting dispatch attempt")
            return False
        db.execute("INSERT INTO delegated_runs VALUES(?,?,?,?,?,'reserved',NULL)",
                   (lease.task_id, lease.attempt, lease.worker_id,
                    lease.backend, lease.opaque_ref))
        return True
    return _atomic(db, work)


def acknowledge(db, lease, external_id, *, now):
    """Record receipt, not successful execution."""
    _clock(now)
    _name(external_id, "external id")

    def work():
        if not _live(db, lease, now):
            raise DelegationRejected("stale or expired coordinator lease")
        row = db.execute(
            "SELECT state,external_id FROM delegated_runs "
            "WHERE task_id=? AND attempt=? AND worker_id=? AND backend=? AND opaque_ref=?",
            (lease.task_id, lease.attempt, lease.worker_id,
             lease.backend, lease.opaque_ref)).fetchone()
        if row is None:
            raise DelegationRejected("not reserved")
        if row[0] == "submitted" and row[1] == external_id:
            return False
        if row[0] != "reserved":
            raise DelegationRejected("conflicting remote acknowledgement")
        db.execute("UPDATE delegated_runs SET state='submitted',external_id=? "
                   "WHERE task_id=? AND attempt=?",
                   (external_id, lease.task_id, lease.attempt))
        return True
    return _atomic(db, work)


def request_cancel(db, lease, *, now):
    """Durable cancellation INTENT; remote termination must be proved separately."""
    _clock(now)

    def work():
        if not _live(db, lease, now):
            raise DelegationRejected("stale or expired coordinator lease")
        row = db.execute(
            "SELECT state,worker_id,backend,opaque_ref FROM delegated_runs "
            "WHERE task_id=? AND attempt=?",
            (lease.task_id, lease.attempt)).fetchone()
        if row is None or tuple(row)[1:] != (
                lease.worker_id, lease.backend, lease.opaque_ref):
            raise DelegationRejected("unknown dispatch")
        if row[0] == "cancel_requested":
            return False
        if row[0] not in ("reserved", "submitted"):
            raise DelegationRejected("not cancellable")
        db.execute("UPDATE delegated_runs SET state='cancel_requested' "
                   "WHERE task_id=? AND attempt=?",
                   (lease.task_id, lease.attempt))
        return True
    return _atomic(db, work)


def mark_orphans(db, *, now):
    """Fence stale records; does not cancel jobs still executing elsewhere."""
    _clock(now)

    def work():
        return db.execute(
            "UPDATE delegated_runs SET state='orphaned' "
            "WHERE state IN ('reserved','submitted','cancel_requested') AND NOT EXISTS "
            "(SELECT 1 FROM tasks WHERE tasks.id=delegated_runs.task_id "
            "AND tasks.attempts=delegated_runs.attempt "
            "AND tasks.worker_id=delegated_runs.worker_id "
            "AND tasks.state='running' AND tasks.lease_until>?)",
            (now,)).rowcount
    return _atomic(db, work)


def prepare_pr(db, lease, branch, head_sha, *, now):
    """Permit one PR create request for a task; ambiguous writes need reconciliation."""
    _clock(now)
    if (type(branch) is not str or _BRANCH.fullmatch(branch) is None
            or branch.startswith("/") or branch.endswith("/")
            or "//" in branch or ".." in branch or "/." in branch):
        raise DelegationRejected("invalid branch")
    if type(head_sha) is not str or _SHA.fullmatch(head_sha) is None:
        raise DelegationRejected("invalid exact head sha")

    def work():
        if not _live(db, lease, now):
            raise DelegationRejected("stale or expired coordinator lease")
        record = db.execute(
            "SELECT state FROM delegated_runs WHERE task_id=? AND attempt=? "
            "AND worker_id=? AND backend=? AND opaque_ref=?",
            (lease.task_id, lease.attempt, lease.worker_id,
             lease.backend, lease.opaque_ref)).fetchone()
        if record is None or record[0] != "submitted":
            raise DelegationRejected("unacknowledged or cancelled run")
        prior = db.execute(
            "SELECT attempt,worker_id,branch,head_sha "
            "FROM delegated_pr_intents WHERE task_id=?",
            (lease.task_id,)).fetchone()
        if prior:
            if tuple(prior) != (lease.attempt, lease.worker_id, branch, head_sha):
                raise DelegationRejected("another PR intent already fenced")
            return False
        db.execute(
            "INSERT INTO delegated_pr_intents VALUES(?,?,?,?,?,'prepared',NULL)",
            (lease.task_id, lease.attempt, lease.worker_id, branch, head_sha))
        return True
    return _atomic(db, work)


def confirm_pr(db, task_id, attempt, head_sha, url):
    """Operator-supplied matching PR URL; verify exact HEAD independently first."""
    _name(task_id, "task id")
    _positive(attempt, "attempt")
    if type(head_sha) is not str or _SHA.fullmatch(head_sha) is None:
        raise DelegationRejected("invalid sha")
    if type(url) is not str or _PR.fullmatch(url) is None:
        raise DelegationRejected("invalid PR URL")

    def work():
        record = db.execute(
            "SELECT attempt,head_sha,state,pr_url "
            "FROM delegated_pr_intents WHERE task_id=?",
            (task_id,)).fetchone()
        if record is None or tuple(record)[:2] != (attempt, head_sha):
            raise DelegationRejected("no matching prepared intent")
        if record[2] == "confirmed":
            if record[3] != url:
                raise DelegationRejected("PR identity collision")
            return False
        db.execute("UPDATE delegated_pr_intents SET state='confirmed',pr_url=? "
                   "WHERE task_id=?", (url, task_id))
        return True
    return _atomic(db, work)


def dispatch_ray(db, lease, approved_remote_function, *, now):
    """Explicit Ray .remote() call on trusted preconfigured function only."""
    if lease.backend != "ray":
        raise DelegationRejected("not a ray ticket")
    if not reserve(db, lease, now=now):
        raise DelegationRejected("dispatch already attempted; reconcile externally")
    return approved_remote_function.remote(encode_wire(lease))


async def dispatch_temporal(db, lease, client, *, task_queue, now):
    """Explicit Temporal Client.start_workflow; caller provisions workflow/queue."""
    if lease.backend != "temporal":
        raise DelegationRejected("not a temporal ticket")
    _name(task_queue, "task queue")
    if not reserve(db, lease, now=now):
        raise DelegationRejected("dispatch already attempted; reconcile externally")
    return await client.start_workflow(
        "mojum_delegate_v1", encode_wire(lease),
        id=f"mojum-{lease.task_id}-attempt-{lease.attempt}",
        task_queue=task_queue)
