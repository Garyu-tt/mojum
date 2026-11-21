#!/usr/bin/env python3
"""Local CPU-only measurement of instrumented provider calls, not worker slots."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import re
import sqlite3
import time
from pathlib import Path

MAX_WORKERS = 300
_MIN_TTL = 30_000
_MAX_TTL = 3_600_000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}\Z")


def connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA journal_mode=WAL")
    return db


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS instrumented_calls (
      request_id TEXT PRIMARY KEY,
      worker_id INTEGER NOT NULL CHECK(worker_id BETWEEN 1 AND 300),
      model TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('active','ok','failed','cancelled','expired')),
      started_ms INTEGER NOT NULL,
      lease_until_ms INTEGER NOT NULL,
      finished_ms INTEGER,
      input_tokens INTEGER,
      output_tokens INTEGER,
      cost_microusd INTEGER
    );
    CREATE UNIQUE INDEX IF NOT EXISTS one_active_rpc_per_worker
      ON instrumented_calls(worker_id) WHERE state='active';
    CREATE TABLE IF NOT EXISTS instrumented_peak (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1),
      max_active INTEGER NOT NULL DEFAULT 0
    );
    INSERT OR IGNORE INTO instrumented_peak(singleton,max_active) VALUES(1,0);
    """)


def _now(now_ms: int | None) -> int:
    if now_ms is None:
        return time.time_ns() // 1_000_000
    if type(now_ms) is not int or now_ms < 0:
        raise ValueError("now_ms must be a non-negative integer")
    return now_ms


def _identity(value: str, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"{label} must be a 1..120 character opaque safe identifier")
    return value


def _worker(worker_id: int) -> int:
    if type(worker_id) is not int or not 1 <= worker_id <= MAX_WORKERS:
        raise ValueError("worker_id must be an integer in 1..300")
    return worker_id


def _ttl(ttl_ms: int) -> int:
    if type(ttl_ms) is not int or not _MIN_TTL <= ttl_ms <= _MAX_TTL:
        raise ValueError("ttl_ms must be an integer in 30000..3600000")
    return ttl_ms


def _usage(value: int | None, label: str, maximum: int) -> int | None:
    if value is None:
        return None  # Unknown is not a measured zero.
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{label} out of bounds")
    return value


def _expire(db: sqlite3.Connection, now: int) -> None:
    db.execute("""
      UPDATE instrumented_calls SET state='expired', finished_ms=?
      WHERE state='active' AND lease_until_ms <= ?
    """, (now, now))


def begin(db: sqlite3.Connection, worker_id: int, request_id: str, model: str,
          *, ttl_ms: int = 120_000, now_ms: int | None = None) -> None:
    """Start a trusted provider RPC span, never just a task lease."""
    _worker(worker_id)
    _identity(request_id, "request_id")
    _identity(model, "model")
    _ttl(ttl_ms)
    now = _now(now_ms)
    db.execute("BEGIN IMMEDIATE")
    try:
        _expire(db, now)
        db.execute("""
          INSERT INTO instrumented_calls
          (request_id,worker_id,model,state,started_ms,lease_until_ms)
          VALUES (?,?,?,'active',?,?)
        """, (request_id, worker_id, model, now, now + ttl_ms))
        count = db.execute(
            "SELECT COUNT(*) FROM instrumented_calls WHERE state='active'"
        ).fetchone()[0]
        db.execute("UPDATE instrumented_peak SET max_active=MAX(max_active,?) "
                   "WHERE singleton=1", (count,))
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


def heartbeat(db: sqlite3.Connection, worker_id: int, request_id: str,
              *, ttl_ms: int = 120_000, now_ms: int | None = None) -> bool:
    """Extend an unexpired RPC span only; expired operations are terminal."""
    _worker(worker_id)
    _identity(request_id, "request_id")
    _ttl(ttl_ms)
    now = _now(now_ms)
    db.execute("BEGIN IMMEDIATE")
    try:
        _expire(db, now)
        changed = db.execute("""
          UPDATE instrumented_calls SET lease_until_ms=?
          WHERE request_id=? AND worker_id=? AND state='active'
        """, (now + ttl_ms, request_id, worker_id)).rowcount
        db.execute("COMMIT")
        return changed == 1
    except Exception:
        db.execute("ROLLBACK")
        raise


def finish(db: sqlite3.Connection, worker_id: int, request_id: str,
           state: str, *, input_tokens: int | None = None,
           output_tokens: int | None = None,
           cost_microusd: int | None = None, now_ms: int | None = None) -> bool:
    """Complete once; usage must be sourced from trusted provider accounting."""
    _worker(worker_id)
    _identity(request_id, "request_id")
    if state not in ("ok", "failed", "cancelled"):
        raise ValueError("state must be ok, failed or cancelled")
    _usage(input_tokens, "input_tokens", 1_000_000_000)
    _usage(output_tokens, "output_tokens", 1_000_000_000)
    _usage(cost_microusd, "cost_microusd", 1_000_000_000)
    now = _now(now_ms)
    db.execute("BEGIN IMMEDIATE")
    try:
        _expire(db, now)
        changed = db.execute("""
          UPDATE instrumented_calls SET state=?, finished_ms=?,
          input_tokens=?, output_tokens=?, cost_microusd=?
          WHERE request_id=? AND worker_id=? AND state='active'
        """, (state, now, input_tokens, output_tokens, cost_microusd,
              request_id, worker_id)).rowcount
        db.execute("COMMIT")
        return changed == 1
    except Exception:
        db.execute("ROLLBACK")
        raise


@contextmanager
def track_call(db: sqlite3.Connection, worker_id: int, request_id: str,
               model: str, *, ttl_ms: int = 120_000):
    """Instrument around the *actual* blocking model API call.

    A trusted provider adapter should call the yielded success callback
    only after an RPC completes. Exceptions are counted without their text.
    """
    begin(db, worker_id, request_id, model, ttl_ms=ttl_ms)
    completed = False

    def success(*, input_tokens: int | None = None,
                output_tokens: int | None = None,
                cost_microusd: int | None = None) -> None:
        nonlocal completed
        if completed:
            raise ValueError("span already completed")
        if not finish(db, worker_id, request_id, "ok",
                      input_tokens=input_tokens, output_tokens=output_tokens,
                      cost_microusd=cost_microusd):
            raise RuntimeError("span expired before completion")
        completed = True

    try:
        yield success
    finally:
        if not completed:
            finish(db, worker_id, request_id, "failed")


def snapshot(db: sqlite3.Connection, *, now_ms: int | None = None) -> dict:
    """Report observed local spans, not actual provider-verified concurrency."""
    now = _now(now_ms)
    db.execute("BEGIN IMMEDIATE")
    try:
        _expire(db, now)
        counts = {r["state"]: r["amount"] for r in db.execute(
            "SELECT state,COUNT(*) AS amount FROM instrumented_calls GROUP BY state"
        )}
        totals = db.execute("""
          SELECT COALESCE(SUM(input_tokens),0) AS input_tokens,
                 COALESCE(SUM(output_tokens),0) AS output_tokens,
                 COALESCE(SUM(cost_microusd),0) AS cost_microusd,
                 COUNT(input_tokens) AS input_token_reports,
                 COUNT(output_tokens) AS output_token_reports,
                 COUNT(cost_microusd) AS cost_reports
          FROM instrumented_calls WHERE state IN ('ok','failed','cancelled')
        """).fetchone()
        peak = db.execute(
            "SELECT max_active FROM instrumented_peak WHERE singleton=1"
        ).fetchone()[0]
        db.execute("COMMIT")
        return {
            "kind": "instrumented_provider_rpc_spans",
            "active": counts.get("active", 0),
            "peak_active": peak,
            "ok": counts.get("ok", 0),
            "failed": counts.get("failed", 0),
            "cancelled": counts.get("cancelled", 0),
            "expired": counts.get("expired", 0),
            "reported_input_tokens": totals["input_tokens"],
            "reported_output_tokens": totals["output_tokens"],
            "reported_cost_microusd": totals["cost_microusd"],
            "input_token_reports": totals["input_token_reports"],
            "output_token_reports": totals["output_token_reports"],
            "cost_reports": totals["cost_reports"],
        }
    except Exception:
        db.execute("ROLLBACK")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    if not Path(args.db).exists():
        parser.error("database does not exist: initialize from trusted adapter")
    db = connect(args.db)
    try:
        create_schema(db)
        print(json.dumps(snapshot(db), sort_keys=True))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
