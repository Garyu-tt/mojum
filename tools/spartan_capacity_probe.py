#!/usr/bin/env python3
"""Local Linux-boot model-call telemetry for Spartan workers (stdlib only).

Instrument *actual provider calls*, not task leases or sleeping mock workers.
The report is self-reported evidence, not provider attestation or a multi-host
concurrency claim. No prompts, credentials or response bodies are stored.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import json
import os
from pathlib import Path
import re
import stat
import sqlite3
import sys
import time

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,127}\Z")
_MAX_REPORT_ROWS = 200_000


def _boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise RuntimeError("Linux boot ID unavailable: cross-process overlap cannot be trusted") from exc
    if not value:
        raise RuntimeError("Linux boot ID unavailable")
    return value


def _require_private_parent(filename: str) -> None:
    """Reject POSIX path components replaceable by other local accounts."""
    if ".." in Path(filename).parts:
        raise PermissionError("probe SQLite parent path traversal forbidden")
    parent = Path(os.path.abspath(filename)).parent
    for depth, directory in enumerate((parent, *parent.parents)):
        info = os.lstat(directory)
        if not stat.S_ISDIR(info.st_mode):
            raise PermissionError("probe SQLite parent directory has a symlink")
        if depth == 0:
            unsafe = info.st_uid != os.geteuid() or bool(info.st_mode & 0o022)
        else:
            unsafe = (info.st_uid not in (0, os.geteuid())
                      or (bool(info.st_mode & 0o022)
                          and not bool(info.st_mode & stat.S_ISVTX)))
        if unsafe:
            raise PermissionError("probe SQLite parent directory is replaceable")


def _require_private_files(filename: str) -> None:
    """Never chmod preexisting SQLite evidence or trust unsafe WAL sidecars."""
    for suffix in ("", "-wal", "-shm"):
        item = filename + suffix
        try:
            info = os.lstat(item)
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077):
            raise PermissionError("probe SQLite file must be owner-only: " + item)


def connect(path: str) -> sqlite3.Connection:
    # Actual worker identities and operation timestamps belong in a PRIVATE DB.
    # Precreating mode 0600 also makes SQLite-created WAL and SHM owner-private.
    # Do not mutate process-wide umask shared by parallel workers.
    if os.name == "posix":
        filename = os.fspath(path)
        if (isinstance(filename, bytes) or filename == ":memory:"
                or filename.startswith("file:")):
            raise ValueError("probe requires a persistent SQLite filesystem path")
        _require_private_parent(filename)
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0))
        try:
            descriptor = os.open(filename, flags, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        _require_private_files(filename)
    db = sqlite3.connect(path, isolation_level=None, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    return db


def initialize(db: sqlite3.Connection, run_id: str) -> None:
    _name(run_id, "run_id")
    boot = _boot_id()
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("CREATE TABLE IF NOT EXISTS metadata (run_id TEXT PRIMARY KEY, boot_id TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS spans (
            op_id TEXT PRIMARY KEY,
            worker_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            start_ns INTEGER NOT NULL CHECK(start_ns > 0),
            end_ns INTEGER CHECK(end_ns > start_ns),
            outcome TEXT CHECK(outcome IN ('success', 'error')),
            input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
            output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
            cost_microusd INTEGER NOT NULL DEFAULT 0 CHECK(cost_microusd >= 0),
            CHECK((end_ns IS NULL AND outcome IS NULL) OR
                  (end_ns IS NOT NULL AND outcome IS NOT NULL))
        )""")
        db.execute("INSERT OR IGNORE INTO metadata VALUES (?, ?)", (run_id, boot))
        _check_run(db, run_id=run_id, boot=boot)
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def _name(value: str, field: str) -> None:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError(f"invalid {field}; use 1..128 safe identifier characters")


def _check_run(db: sqlite3.Connection, *, run_id: str | None = None,
               boot: str | None = None) -> str:
    row = db.execute("SELECT run_id, boot_id FROM metadata").fetchone()
    if row is None:
        raise ValueError("probe is not initialized")
    if (run_id is not None and row["run_id"] != run_id) or row["boot_id"] != (boot or _boot_id()):
        raise RuntimeError("run or Linux boot mismatch; use a fresh database for this run")
    return row["run_id"]


def begin(db: sqlite3.Connection, op_id: str, worker_id: str, model_id: str,
          *, now_ns: int | None = None) -> None:
    for field, value in (("op_id", op_id), ("worker_id", worker_id), ("model_id", model_id)):
        _name(value, field)
    timestamp = time.monotonic_ns() if now_ns is None else now_ns
    if type(timestamp) is not int or timestamp <= 0:
        raise ValueError("invalid monotonic timestamp")
    db.execute("BEGIN IMMEDIATE")
    try:
        _check_run(db)
        db.execute("INSERT INTO spans(op_id, worker_id, model_id, start_ns) VALUES (?, ?, ?, ?)",
                   (op_id, worker_id, model_id, timestamp))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def finish(db: sqlite3.Connection, op_id: str, outcome: str, *,
           input_tokens: int = 0, output_tokens: int = 0, cost_microusd: int = 0,
           now_ns: int | None = None) -> None:
    _name(op_id, "op_id")
    if outcome not in ("success", "error"):
        raise ValueError("outcome must be success or error")
    for field, value in (("input_tokens", input_tokens), ("output_tokens", output_tokens),
                         ("cost_microusd", cost_microusd)):
        if type(value) is not int or not 0 <= value <= 10**12:
            raise ValueError(f"invalid {field}")
    timestamp = time.monotonic_ns() if now_ns is None else now_ns
    if type(timestamp) is not int or timestamp <= 0:
        raise ValueError("invalid monotonic timestamp")
    db.execute("BEGIN IMMEDIATE")
    try:
        _check_run(db)
        result = db.execute("""UPDATE spans SET end_ns=?, outcome=?, input_tokens=?,
            output_tokens=?, cost_microusd=?
            WHERE op_id=? AND end_ns IS NULL AND start_ns < ?""",
            (timestamp, outcome, input_tokens, output_tokens, cost_microusd, op_id, timestamp))
        if result.rowcount != 1:
            raise ValueError("unknown, already finished, or non-monotonic operation")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def _peak(rows: list[sqlite3.Row], *, successful_only: bool) -> tuple[int, int]:
    events = []
    for row in rows:
        if row["end_ns"] is None or (successful_only and row["outcome"] != "success"):
            continue
        events.append((row["start_ns"], 1, row["worker_id"]))
        events.append((row["end_ns"], -1, row["worker_id"]))
    # Half-open [start, end) interval: ends precede starts for tied timestamps.
    events.sort(key=lambda event: (event[0], event[1]))
    workers = Counter()
    active = peak_active = peak_workers = 0
    for _, delta, worker in events:
        if delta == 1:
            workers[worker] += 1
            active += 1
        else:
            workers[worker] -= 1
            active -= 1
            if workers[worker] == 0:
                del workers[worker]
        peak_active = max(peak_active, active)
        peak_workers = max(peak_workers, len(workers))
    return peak_active, peak_workers


def report(db: sqlite3.Connection) -> dict:
    run_id = _check_run(db)
    rows = db.execute("SELECT * FROM spans LIMIT ?", (_MAX_REPORT_ROWS + 1,)).fetchall()
    if len(rows) > _MAX_REPORT_ROWS:
        raise ValueError("report exceeds bounded 200000-span run; rotate database")
    completed = [row for row in rows if row["end_ns"] is not None]
    successes = [row for row in completed if row["outcome"] == "success"]
    peak_calls, peak_workers = _peak(rows, successful_only=False)
    peak_success_calls, peak_success_workers = _peak(rows, successful_only=True)
    elapsed_ns = (max(row["end_ns"] for row in completed) -
                  min(row["start_ns"] for row in completed)) if completed else 0
    return {
        "run_id": run_id,
        "scope": "single_linux_boot",
        "evidence": "self_reported_provider_call_spans_not_provider_attestation",
        "started_operations": len(rows),
        "completed_operations": len(completed),
        "successful_operations": len(successes),
        "failed_operations": len(completed) - len(successes),
        "unfinished_operations_excluded_from_peak": len(rows) - len(completed),
        "unique_successful_workers": len({row["worker_id"] for row in successes}),
        "peak_completed_calls": peak_calls,
        "peak_completed_workers": peak_workers,
        "peak_successful_calls": peak_success_calls,
        "peak_simultaneous_successful_workers": peak_success_workers,
        "input_tokens_reported": sum(row["input_tokens"] for row in completed),
        "output_tokens_reported": sum(row["output_tokens"] for row in completed),
        "cost_microusd_reported": sum(row["cost_microusd"] for row in completed),
        "elapsed_seconds": round(elapsed_ns / 1e9, 6),
        "successful_calls_per_minute": round(len(successes) * 60e9 / elapsed_ns, 3)
        if elapsed_ns >= 1_000_000 else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="local private SQLite metrics path")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("init")
    p.add_argument("--run", required=True)
    p = commands.add_parser("begin")
    p.add_argument("--op", required=True)
    p.add_argument("--worker", required=True)
    p.add_argument("--model", required=True)
    p = commands.add_parser("finish")
    p.add_argument("--op", required=True)
    p.add_argument("--outcome", choices=("success", "error"), required=True)
    p.add_argument("--input-tokens", type=int, default=0)
    p.add_argument("--output-tokens", type=int, default=0)
    p.add_argument("--cost-microusd", type=int, default=0)
    p = commands.add_parser("report")
    p.add_argument("--min-simultaneous-successful-workers", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        with closing(connect(args.db)) as db:
            if args.command == "init":
                initialize(db, args.run)
            elif args.command == "begin":
                begin(db, args.op, args.worker, args.model)
            elif args.command == "finish":
                finish(db, args.op, args.outcome, input_tokens=args.input_tokens,
                       output_tokens=args.output_tokens, cost_microusd=args.cost_microusd)
            else:
                if not 0 <= args.min_simultaneous_successful_workers <= 300:
                    raise ValueError("required simultaneous workers must be 0..300")
                result = report(db)
                print(json.dumps(result, sort_keys=True))
                if result["peak_simultaneous_successful_workers"] < args.min_simultaneous_successful_workers:
                    return 2
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"spartan_capacity_probe: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
