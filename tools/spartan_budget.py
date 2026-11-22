#!/usr/bin/env python3
"""Credential-free, fail-closed pre-dispatch budget ledger for Spartan workers.

A reservation is a permanent debit, including on crash/timeout. This prevents
uncertain delivery from accidentally releasing provider spend. The ledger is NOT
a substitute for provider-side token or billing caps.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
from pathlib import Path

MAX_VALUE = 10**12
MAX_WORKERS = 300
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}\Z", re.ASCII)


class BudgetError(ValueError):
    """Invalid or exhausted budget; no model request should be dispatched."""


def _integer(value: int, name: str, low: int, high: int = MAX_VALUE) -> int:
    if type(value) is not int or not low <= value <= high:
        raise BudgetError(f"{name} must be an integer in {low}..{high}")
    return value


def _id(value: str) -> str:
    if type(value) is not str or not REQUEST_ID.fullmatch(value):
        raise BudgetError("request_id must be 1..120 safe ASCII characters")
    return value


def _check_private_ledger_files(filename: str) -> None:
    """Refuse insecure existing SQLite DB, WAL, SHM or journal files."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            info = os.lstat(filename + suffix)
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or bool(info.st_mode & 0o077)
                or info.st_nlink != 1):
            raise BudgetError("SQLite ledger file or sidecar is not owner-private")


def _private_ledger_path(path: str | Path) -> str:
    """Require a private POSIX directory chain before any SQLite pathname open.

    Other-UID replacement is excluded by a mode-0700 leaf directory and
    nonreplaceable ancestors. Same-UID/root races are outside this contract.
    """
    if (os.name != "posix"
            or not all(hasattr(os, flag) for flag in
                       ("O_NOFOLLOW", "O_CLOEXEC", "O_EXCL"))):
        raise BudgetError("private POSIX ledger storage is required")
    filename = os.fspath(path)
    if (not isinstance(filename, str) or not filename
            or not Path(filename).is_absolute()
            or filename.startswith("file:")
            or ".." in Path(filename).parts):
        raise BudgetError("ledger requires an absolute private filesystem path")
    target = Path(filename)
    try:
        for index, directory in enumerate((target.parent, *target.parent.parents)):
            info = os.lstat(directory)
            if not stat.S_ISDIR(info.st_mode):
                raise BudgetError("ledger directory chain contains a symlink")
            if index == 0:
                unsafe = (info.st_uid != os.geteuid()
                          or bool(info.st_mode & 0o077))
            else:
                unsafe = (info.st_uid not in (0, os.geteuid())
                          or (bool(info.st_mode & 0o022)
                              and not bool(info.st_mode & stat.S_ISVTX)))
            if unsafe:
                raise BudgetError("ledger directory chain is not owner-private")
        # Fail before creating anything when a pre-existing sidecar is unsafe.
        _check_private_ledger_files(filename)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            descriptor = os.open(filename, flags, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        _check_private_ledger_files(filename)
    except OSError as exc:
        raise BudgetError("cannot safely inspect or open private SQLite ledger") from exc
    return filename


def connect(path: str | Path) -> sqlite3.Connection:
    filename = _private_ledger_path(path)
    try:
        db = sqlite3.connect(filename, timeout=30, isolation_level=None)
    except sqlite3.Error as exc:
        raise BudgetError("cannot open private SQLite ledger") from exc
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        _check_private_ledger_files(filename)
        return db
    except BaseException:
        db.close()
        raise


def schema(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS budget (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        token_limit INTEGER NOT NULL CHECK (token_limit BETWEEN 1 AND 1000000000000),
        micro_usd_limit INTEGER NOT NULL CHECK (micro_usd_limit BETWEEN 0 AND 1000000000000),
        frozen INTEGER NOT NULL DEFAULT 0 CHECK (frozen IN (0, 1))
    );
    CREATE TABLE IF NOT EXISTS requests (
        request_id TEXT PRIMARY KEY,
        worker INTEGER NOT NULL CHECK (worker BETWEEN 1 AND 300),
        reserved_tokens INTEGER NOT NULL CHECK (reserved_tokens BETWEEN 1 AND 1000000000000),
        reserved_micro_usd INTEGER NOT NULL CHECK (reserved_micro_usd BETWEEN 0 AND 1000000000000),
        state TEXT NOT NULL CHECK (state IN ('reserved', 'settled')),
        actual_tokens INTEGER,
        actual_micro_usd INTEGER,
        CHECK ((state = 'reserved' AND actual_tokens IS NULL AND actual_micro_usd IS NULL)
            OR (state = 'settled' AND actual_tokens IS NOT NULL AND actual_micro_usd IS NOT NULL))
    );
    """)


def configure(db: sqlite3.Connection, token_limit: int, micro_usd_limit: int) -> dict:
    _integer(token_limit, "token_limit", 1)
    _integer(micro_usd_limit, "micro_usd_limit", 0)
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("SELECT token_limit, micro_usd_limit FROM budget WHERE id=1").fetchone()
        if row is None:
            db.execute("INSERT INTO budget(id, token_limit, micro_usd_limit) VALUES (1, ?, ?)", (token_limit, micro_usd_limit))
        elif (row["token_limit"], row["micro_usd_limit"]) != (token_limit, micro_usd_limit):
            raise BudgetError("budget is immutable; create a new ledger for a different budget")
        db.execute("COMMIT")
        return {"token_limit": token_limit, "micro_usd_limit": micro_usd_limit}
    except BaseException:
        db.execute("ROLLBACK")
        raise


def reserve(db: sqlite3.Connection, request_id: str, worker: int,
            tokens: int, micro_usd: int) -> dict:
    """Return dispatch_allowed=True ONCE. A duplicate must never call a provider."""
    _id(request_id)
    _integer(worker, "worker", 1, MAX_WORKERS)
    _integer(tokens, "tokens", 1)
    _integer(micro_usd, "micro_usd", 0)
    db.execute("BEGIN IMMEDIATE")
    try:
        policy = db.execute("SELECT frozen FROM budget WHERE id=1").fetchone()
        if policy is None or policy["frozen"]:
            raise BudgetError("budget missing or frozen after provider overrun")
        existing = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if existing is not None:
            if (existing["worker"], existing["reserved_tokens"],
                    existing["reserved_micro_usd"]) != (worker, tokens, micro_usd):
                raise BudgetError("request_id reused with different reservation")
            result = {"request_id": request_id, "dispatch_allowed": False,
                      "state": existing["state"]}
        else:
            limits = db.execute("SELECT token_limit, micro_usd_limit FROM budget WHERE id=1").fetchone()
            if limits is None:
                raise BudgetError("budget is not configured")
            used = db.execute("SELECT COALESCE(SUM(reserved_tokens),0) AS tokens, "
                              "COALESCE(SUM(reserved_micro_usd),0) AS usd FROM requests").fetchone()
            if (tokens > limits["token_limit"] - used["tokens"] or
                    micro_usd > limits["micro_usd_limit"] - used["usd"]):
                raise BudgetError("budget exhausted; request must not be dispatched")
            db.execute("INSERT INTO requests VALUES (?, ?, ?, ?, 'reserved', NULL, NULL)",
                       (request_id, worker, tokens, micro_usd))
            result = {"request_id": request_id, "dispatch_allowed": True,
                      "state": "reserved"}
        db.execute("COMMIT")
        return result
    except BaseException:
        db.execute("ROLLBACK")
        raise


def settle(db: sqlite3.Connection, request_id: str, worker: int,
           actual_tokens: int, actual_micro_usd: int) -> dict:
    _id(request_id)
    _integer(worker, "worker", 1, MAX_WORKERS)
    _integer(actual_tokens, "actual_tokens", 0)
    _integer(actual_micro_usd, "actual_micro_usd", 0)
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if row is None or row["worker"] != worker:
            raise BudgetError("unknown request_id or wrong worker")
        if (actual_tokens > row["reserved_tokens"] or
                actual_micro_usd > row["reserved_micro_usd"]):
            # Persist freeze before raising; never roll it back on the error path.
            db.execute("UPDATE budget SET frozen=1 WHERE id=1")
            db.execute("COMMIT")
            raise BudgetError("reported usage exceeds reservation; ledger frozen")
        if row["state"] == "settled":
            if (row["actual_tokens"], row["actual_micro_usd"]) != (actual_tokens, actual_micro_usd):
                # Contradictory provider evidence invalidates the spending bound.
                # Commit the freeze before raising: exception handling must not
                # rollback it or admit another chargeable request.
                db.execute("UPDATE budget SET frozen=1 WHERE id=1")
                db.execute("COMMIT")
                raise BudgetError("settlement receipt changed; ledger frozen")
            new = False
        else:
            db.execute("UPDATE requests SET state='settled', actual_tokens=?, "
                       "actual_micro_usd=? WHERE request_id=?",
                       (actual_tokens, actual_micro_usd, request_id))
            new = True
        db.execute("COMMIT")
        return {"request_id": request_id, "new_settlement": new, "state": "settled"}
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def status(db: sqlite3.Connection) -> dict:
    row = db.execute("SELECT token_limit, micro_usd_limit, frozen FROM budget WHERE id=1").fetchone()
    if row is None:
        raise BudgetError("budget is not configured")
    sums = db.execute("""
        SELECT COUNT(*) AS requests,
               COALESCE(SUM(CASE WHEN state='reserved' THEN 1 ELSE 0 END),0) AS unsettled,
               COALESCE(SUM(reserved_tokens),0) AS debited_tokens,
               COALESCE(SUM(reserved_micro_usd),0) AS debited_micro_usd,
               COALESCE(SUM(actual_tokens),0) AS reported_tokens,
               COALESCE(SUM(actual_micro_usd),0) AS reported_micro_usd
        FROM requests
    """).fetchone()
    return {"token_limit": row["token_limit"], "micro_usd_limit": row["micro_usd_limit"],
            "frozen": bool(row["frozen"]),
            **dict(sums), "dispatch_active": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="durable private SQLite ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init")
    p.add_argument("--tokens", type=int, required=True)
    p.add_argument("--micro-usd", type=int, required=True)
    p = sub.add_parser("reserve")
    p.add_argument("--id", required=True)
    p.add_argument("--worker", type=int, required=True)
    p.add_argument("--tokens", type=int, required=True)
    p.add_argument("--micro-usd", type=int, required=True)
    p = sub.add_parser("settle")
    p.add_argument("--id", required=True)
    p.add_argument("--worker", type=int, required=True)
    p.add_argument("--actual-tokens", type=int, required=True)
    p.add_argument("--actual-micro-usd", type=int, required=True)
    sub.add_parser("status")
    args = parser.parse_args(argv)
    try:
        db = connect(args.db)
    except BudgetError as exc:
        parser.exit(2, f"budget denied: {exc}\\n")
    try:
        schema(db)
        if args.command == "init":
            result = configure(db, args.tokens, args.micro_usd)
        elif args.command == "reserve":
            result = reserve(db, args.id, args.worker, args.tokens, args.micro_usd)
        elif args.command == "settle":
            result = settle(db, args.id, args.worker, args.actual_tokens, args.actual_micro_usd)
        else:
            result = status(db)
        print(json.dumps(result, sort_keys=True))
        return 0
    except BudgetError as exc:
        parser.exit(2, f"budget denied: {exc}\n")
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
