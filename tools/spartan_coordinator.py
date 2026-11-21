#!/usr/bin/env python3
"""Mojum Spartan dispatcher: durable, atomic task leases, no model credentials."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

MAX_SLOTS = 300
MAX_ATTEMPTS = 3
VALID_STATES = ("queued", "running", "review", "blocked")


def connect(path: str) -> sqlite3.Connection:
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
        if not isinstance(item.get("priority", 0), int):
            raise ValueError("priority must be an integer")
        if len(json.dumps(item.get("payload", {}))) > 8192:
            raise ValueError("payload is too large")
    db.execute("BEGIN IMMEDIATE")
    try:
        before = db.total_changes
        for item in items:
            db.execute(
                "INSERT OR IGNORE INTO tasks(id, scope, priority, payload, updated_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (item["id"], item["scope"], item.get("priority", 0),
                 json.dumps(item.get("payload", {}), sort_keys=True), now),
            )
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
                  AND NOT EXISTS (
                    SELECT 1 FROM tasks AS active
                    WHERE active.scope=t.scope AND active.state='running'
                  )
                ORDER BY t.priority DESC, t.updated_at ASC, t.id ASC LIMIT 1
            """).fetchone()
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


def heartbeat(db: sqlite3.Connection, worker_id: int, task_id: str, now: int, ttl: int = 900) -> bool:
    if ttl < 30 or ttl > 86400:
        raise ValueError("ttl must be 30..86400 seconds")
    db.execute("BEGIN IMMEDIATE")
    try:
        changed = db.execute(
            "UPDATE tasks SET lease_until=?, updated_at=? "
            "WHERE id=? AND worker_id=? AND state='running' AND lease_until>?",
            (now + ttl, now, task_id, worker_id, now),
        ).rowcount
        db.execute("COMMIT")
        return changed == 1
    except Exception:
        db.execute("ROLLBACK")
        raise


def finish(db: sqlite3.Connection, worker_id: int, task_id: str, result: str,
           now: int, artifact: str = "") -> bool:
    if result not in ("review", "blocked", "queued"):
        raise ValueError("worker may only submit to review, block or requeue")
    if result == "review" and not artifact.strip():
        raise ValueError("review requires a PR or patch artifact")
    if len(artifact) > 500:
        raise ValueError("artifact too long")
    db.execute("BEGIN IMMEDIATE")
    try:
        changed = db.execute(
            "UPDATE tasks SET state=?, worker_id=NULL, lease_until=NULL, "
            "artifact=?, updated_at=? WHERE id=? AND worker_id=? "
            "AND state='running' AND lease_until>?",
            (result, artifact or None, now, task_id, worker_id, now),
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
    fi = commands.add_parser("finish")
    fi.add_argument("--worker", type=int, required=True)
    fi.add_argument("--task", required=True)
    fi.add_argument("--result", choices=("review", "blocked", "queued"), required=True)
    fi.add_argument("--artifact", default="")
    commands.add_parser("status")
    args = parser.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    db = connect(args.db)
    create_schema(db)
    now = int(time.time())
    if args.action == "init":
        result = init(db, args.slots, now)
    elif args.action == "enqueue":
        with open(args.json, encoding="utf-8") as stream:
            items = json.load(stream)
        if not isinstance(items, list):
            raise ValueError("enqueue input must be an array")
        result = enqueue(db, items, now)
    elif args.action == "claim":
        result = claim(db, args.worker, now, args.ttl)
    elif args.action == "heartbeat":
        result = {"renewed": heartbeat(db, args.worker, args.task, now, args.ttl)}
    elif args.action == "finish":
        result = {"updated": finish(db, args.worker, args.task, args.result, now, args.artifact)}
    else:
        result = status(db)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
