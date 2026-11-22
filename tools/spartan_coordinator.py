#!/usr/bin/env python3
"""Mojum Spartan dispatcher: durable, atomic task leases, no model credentials."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import time
from pathlib import Path

MAX_SLOTS = 300
MAX_ATTEMPTS = 3
VALID_STATES = ("queued", "running", "review", "blocked")


def _reject_nonfinite_constant(value: str):
    raise ValueError(f"non-finite JSON constant not allowed: {value}")


def _check_private_file(path: str) -> None:
    """Existing SQLite database and WAL sidecars must be owner-private."""
    for suffix in ("", "-wal", "-shm"):
        filename = path + suffix
        try:
            info = os.lstat(filename)
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077):
            raise PermissionError(
                f"Spartan SQLite file must be a regular owner-only file (0600): {filename}"
            )


def _check_private_parent(path: str) -> None:
    """Refuse writable or replaceable SQLite parent directories.

    A file-mode check followed by sqlite3.connect(path) is unsafe if another
    local account can replace the checked path between those operations.
    """
    # Lexical abspath() collapses '..' before resolving symlinks, while
    # sqlite3.connect() follows the original path in filesystem order.
    # Reject this ambiguity before checking any directory inode.
    if ".." in Path(path).parts:
        raise PermissionError("Spartan SQLite parent directory path traversal is forbidden")
    # An unsafe ancestor permits replacing even an owner-only parent.
    parent = Path(os.path.abspath(path)).parent
    for depth, directory in enumerate((parent, *parent.parents)):
        info = os.lstat(directory)
        if not stat.S_ISDIR(info.st_mode):
            raise PermissionError("Spartan SQLite parent directory chain contains a symlink")
        if depth == 0:
            unsafe = info.st_uid != os.geteuid() or bool(info.st_mode & 0o022)
        else:
            unsafe = (info.st_uid not in (0, os.geteuid())
                      or (bool(info.st_mode & 0o022)
                          and not bool(info.st_mode & stat.S_ISVTX)))
        if unsafe:
            raise PermissionError(
                f"Spartan SQLite parent directory chain is replaceable: {directory}"
            )


def connect(path: str) -> sqlite3.Connection:
    # The SQLite connection stores user-supplied prompts and exact work scopes.
    # sqlite3.connect creates a new DB using the process umask (often 0644).
    # Creating the DB as 0600 first also makes SQLite's WAL and SHM 0600.
    # Do not change a process-wide umask: parallel workers share it.
    if os.name == "posix" and os.fspath(path) != ":memory:":
        filename = os.fspath(path)
        if isinstance(filename, bytes) or filename.startswith("file:"):
            raise ValueError("Spartan SQLite requires a filesystem path")
        _check_private_parent(filename)
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0))
        try:
            descriptor = os.open(filename, flags, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        _check_private_file(filename)
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS agents (
        id INTEGER PRIMARY KEY CHECK(id BETWEEN 1 AND 300),
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        scope TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 0,
        payload TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL DEFAULT 'queued'
          CHECK(state IN ('queued', 'running', 'review', 'blocked')),
        worker_id INTEGER REFERENCES agents(id),
        lease_until INTEGER,
        attempts INTEGER NOT NULL DEFAULT 0,
        artifact TEXT,
        updated_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS tasks_dispatch ON tasks(state, priority DESC, updated_at);
    CREATE INDEX IF NOT EXISTS tasks_scope ON tasks(scope, state);
    """)


def init(db: sqlite3.Connection, slots: int, now: int) -> dict:
    if not 1 <= slots <= MAX_SLOTS:
        raise ValueError("slots must be between 1 and 300")
    with db:
        db.executemany(
            "INSERT OR IGNORE INTO agents(id, created_at) VALUES(?, ?)",
            ((i, now) for i in range(1, slots + 1)),
        )
    return {"slots": db.execute("SELECT COUNT(*) FROM agents").fetchone()[0]}


def enqueue(db: sqlite3.Connection, items: list[dict], now: int) -> dict:
    seen = set()
    for item in items:
        task_id, scope = item.get("id"), item.get("scope")
        if not isinstance(task_id, str) or not 1 <= len(task_id) <= 120:
            raise ValueError("task id must be 1..120 chars")
        if not isinstance(scope, str) or not 1 <= len(scope) <= 120:
            raise ValueError("task scope must be 1..120 chars")
        if task_id in seen:
            raise ValueError("duplicate id in batch")
        seen.add(task_id)
        # bool is a subclass of int, but a boolean is not a task priority.
        # SQLite converts True to 1, confusing immutable enqueue identity.
        if type(item.get("priority", 0)) is not int:
            raise ValueError("priority must be an integer")
        if len(json.dumps(item.get("payload", {}), sort_keys=True, allow_nan=False)) > 8192:
            raise ValueError("payload is too large")
    db.execute("BEGIN IMMEDIATE")
    try:
        before = db.total_changes
        for item in items:
            priority = item.get("priority", 0)
            payload = json.dumps(item.get("payload", {}), sort_keys=True, allow_nan=False)
            inserted = db.execute(
                "INSERT OR IGNORE INTO tasks(id, scope, priority, payload, updated_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (item["id"], item["scope"], priority, payload, now),
            ).rowcount
            if not inserted:
                existing = db.execute(
                    "SELECT scope, priority, payload FROM tasks WHERE id=?",
                    (item["id"],),
                ).fetchone()
                if existing is None or (
                    existing["scope"], existing["priority"], existing["payload"]
                ) != (item["scope"], priority, payload):
                    raise ValueError("task id reused with different scope, priority or payload")
        added = db.total_changes - before
        db.execute("COMMIT")
        return {"added": added, "existing": len(items) - added}
    except Exception:
        db.execute("ROLLBACK")
        raise


def expire(db: sqlite3.Connection, now: int) -> None:
    db.execute(
        "UPDATE tasks SET state='blocked', worker_id=NULL, lease_until=NULL, "
        "updated_at=? WHERE state='running' AND lease_until<=? AND attempts>=?",
        (now, now, MAX_ATTEMPTS),
    )
    # Reconcile ledgers written by older coordinator versions as well as any
    # externally imported state.  An exhausted queued row must never receive
    # a fourth lease merely because it was not left in ``running`` state.
    db.execute(
        "UPDATE tasks SET state='blocked', worker_id=NULL, lease_until=NULL, "
        "updated_at=? WHERE state='queued' AND attempts>=?",
        (now, MAX_ATTEMPTS),
    )
    db.execute(
        "UPDATE tasks SET state='queued', worker_id=NULL, lease_until=NULL, "
        "updated_at=? WHERE state='running' AND lease_until<=?",
        (now, now),
    )


def claim(db: sqlite3.Connection, worker_id: int, now: int, ttl: int = 900) -> dict | None:
    if ttl < 30 or ttl > 86400:
        raise ValueError("ttl must be 30..86400 seconds")
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT 1 FROM agents WHERE id=?", (worker_id,)).fetchone() is None:
            raise ValueError("unknown agent id")
        expire(db, now)
        row = db.execute(
            "SELECT * FROM tasks WHERE state='running' AND worker_id=?",
            (worker_id,),
        ).fetchone()
        if row is None:
            row = db.execute("""
                SELECT t.* FROM tasks AS t
                WHERE t.state='queued'
                  AND t.attempts < ?
                  AND NOT EXISTS (
                    SELECT 1 FROM tasks AS active
                    WHERE active.scope=t.scope AND active.state='running'
                  )
                ORDER BY t.priority DESC, t.updated_at ASC, t.id ASC LIMIT 1
            """, (MAX_ATTEMPTS,)).fetchone()
            if row:
                db.execute(
                    "UPDATE tasks SET state='running', worker_id=?, lease_until=?, "
                    "attempts=attempts+1, updated_at=? WHERE id=?",
                    (worker_id, now + ttl, now, row["id"]),
                )
                row = db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
        db.execute("COMMIT")
        return dict(row) if row else None
    except Exception:
        db.execute("ROLLBACK")
        raise


def heartbeat(db: sqlite3.Connection, worker_id: int, task_id: str, now: int,
              ttl: int = 900, *, attempt: int) -> bool:
    if ttl < 30 or ttl > 86400:
        raise ValueError("ttl must be 30..86400 seconds")
    if type(attempt) is not int or attempt < 1:
        raise ValueError("attempt fencing token must be a positive integer")
    db.execute("BEGIN IMMEDIATE")
    try:
        changed = db.execute(
            "UPDATE tasks SET lease_until=?, updated_at=? "
            "WHERE id=? AND worker_id=? AND attempts=? "
            "AND state='running' AND lease_until>?",
            (now + ttl, now, task_id, worker_id, attempt, now),
        ).rowcount
        db.execute("COMMIT")
        return changed == 1
    except Exception:
        db.execute("ROLLBACK")
        raise


def finish(db: sqlite3.Connection, worker_id: int, task_id: str, result: str,
           now: int, artifact: str = "", *, attempt: int) -> bool:
    if result not in ("review", "blocked", "queued"):
        raise ValueError("worker may only submit to review, block or requeue")
    if result == "review" and not artifact.strip():
        raise ValueError("review requires a PR or patch artifact")
    if len(artifact) > 500:
        raise ValueError("artifact too long")
    if type(attempt) is not int or attempt < 1:
        raise ValueError("attempt fencing token must be a positive integer")
    db.execute("BEGIN IMMEDIATE")
    try:
        if result == "queued":
            running = db.execute(
                "SELECT attempts FROM tasks WHERE id=? AND worker_id=? "
                "AND attempts=? AND state='running' AND lease_until>?",
                (task_id, worker_id, attempt, now),
            ).fetchone()
            if running is not None and running["attempts"] >= MAX_ATTEMPTS:
                raise ValueError("retry budget exhausted; finish as blocked")
        changed = db.execute(
            "UPDATE tasks SET state=?, worker_id=NULL, lease_until=NULL, "
            "artifact=?, updated_at=? WHERE id=? AND worker_id=? AND attempts=? "
            "AND state='running' AND lease_until>?",
            (result, artifact or None, now, task_id, worker_id, attempt, now),
        ).rowcount
        db.execute("COMMIT")
        return changed == 1
    except Exception:
        db.execute("ROLLBACK")
        raise


def status(db: sqlite3.Connection) -> dict:
    states = {r["state"]: r["cnt"] for r in db.execute(
        "SELECT state, COUNT(*) AS cnt FROM tasks GROUP BY state"
    )}
    return {
        "slots": db.execute("SELECT COUNT(*) FROM agents").fetchone()[0],
        "states": {name: states.get(name, 0) for name in VALID_STATES},
        "active_scopes": db.execute(
            "SELECT COUNT(DISTINCT scope) FROM tasks WHERE state='running'"
        ).fetchone()[0],
        "model_processes": "not managed by dispatcher",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="spartan-state.sqlite3")
    commands = parser.add_subparsers(dest="action", required=True)
    init_p = commands.add_parser("init")
    init_p.add_argument("--slots", type=int, default=300)
    enq = commands.add_parser("enqueue")
    enq.add_argument("--json", required=True, help="JSON file containing list of task objects")
    cl = commands.add_parser("claim")
    cl.add_argument("--worker", type=int, required=True)
    cl.add_argument("--ttl", type=int, default=900)
    hb = commands.add_parser("heartbeat")
    hb.add_argument("--worker", type=int, required=True)
    hb.add_argument("--task", required=True)
    hb.add_argument("--ttl", type=int, default=900)
    hb.add_argument("--attempt", type=int, required=True,
                    help="fencing attempt number returned by claim")
    fi = commands.add_parser("finish")
    fi.add_argument("--worker", type=int, required=True)
    fi.add_argument("--task", required=True)
    fi.add_argument("--result", choices=("review", "blocked", "queued"), required=True)
    fi.add_argument("--artifact", default="")
    fi.add_argument("--attempt", type=int, required=True,
                    help="fencing attempt number returned by claim")
    commands.add_parser("status")
    args = parser.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    db = connect(args.db)
    try:
        create_schema(db)
        now = int(time.time())
        if args.action == "init":
            result = init(db, args.slots, now)
        elif args.action == "enqueue":
            with open(args.json, encoding="utf-8") as stream:
                items = json.load(stream, parse_constant=_reject_nonfinite_constant)
            if not isinstance(items, list):
                raise ValueError("enqueue input must be an array")
            result = enqueue(db, items, now)
        elif args.action == "claim":
            result = claim(db, args.worker, now, args.ttl)
        elif args.action == "heartbeat":
            result = {"renewed": heartbeat(db, args.worker, args.task, now, args.ttl,
                                           attempt=args.attempt)}
        elif args.action == "finish":
            result = {"updated": finish(db, args.worker, args.task, args.result, now,
                                        args.artifact, attempt=args.attempt)}
        else:
            result = status(db)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0

    finally:
        db.close()

if __name__ == "__main__":
    raise SystemExit(main())
