"""Durable, credential-free admission accounting for experimental model workers.

All money is integer micro-USD. Reservations are conservative: even a timed-out
request consumes its entire allowance until an operator reconciles it. This is
not a model-provider adapter or a substitute for server-side spending limits.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import os
import sqlite3
import stat
import time
from pathlib import Path


class BudgetDenied(RuntimeError):
    """The requested model operation cannot safely be admitted."""


@dataclass(frozen=True)
class BudgetCaps:
    requests_per_minute: int
    tokens_per_minute: int
    usd_micros_per_utc_day: int

    def __post_init__(self) -> None:
        for value in (
            self.requests_per_minute,
            self.tokens_per_minute,
            self.usd_micros_per_utc_day,
        ):
            if type(value) is not int or value <= 0 or value > 10**12:
                raise ValueError("budget caps must be positive bounded integers")


@dataclass(frozen=True)
class Admission:
    request_digest: str
    model: str
    reserved_tokens: int
    reserved_usd_micros: int
    duplicate: bool


def _check_private_sqlite_path(path: str) -> None:
    """Restrict access to a local budget ledger before any SQLite I/O."""
    if ".." in Path(path).parts:
        raise BudgetDenied("budget ledger parent traversal is forbidden")
    parent = Path(os.path.abspath(path)).parent
    for depth, directory in enumerate((parent, *parent.parents)):
        try:
            info = os.lstat(directory)
        except OSError as exc:
            raise BudgetDenied("cannot inspect budget ledger directory") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise BudgetDenied("budget ledger directory chain contains a symlink")
        if depth == 0:
            # A private ledger directory must not disclose its entries to other UIDs.
            unsafe = info.st_uid != os.geteuid() or bool(info.st_mode & 0o077)
        else:
            unsafe = (info.st_uid not in (0, os.geteuid())
                      or (bool(info.st_mode & 0o022)
                          and not bool(info.st_mode & stat.S_ISVTX)))
        if unsafe:
            raise BudgetDenied("budget ledger directory chain is replaceable")

    # Never repair permissions or follow a preexisting symlink: the contents
    # could be an audit record belonging to a different local user.
    for suffix in ("", "-wal", "-shm"):
        name = path + suffix
        try:
            info = os.lstat(name)
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077):
            raise BudgetDenied(
                "budget ledger files must be regular owner-only files (0600)"
            )


class BudgetLedger:
    """SQLite-backed concurrent fixed-window rate and daily budget gate.

    All callers must use the *same database and caps*. Keep each operation's
    idempotency key stable across retries. An uncertain request is never
    refunded automatically; it requires external reconciliation.
    """

    def __init__(self, path: str | Path, caps: BudgetCaps):
        self.path = str(path)
        if (not self.path or self.path == ":memory:"
                or self.path.startswith("file:") or "\x00" in self.path):
            raise ValueError("budget ledger requires a persistent filesystem path")
        self.caps = caps
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS budget_settings (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    rpm INTEGER NOT NULL, tpm INTEGER NOT NULL,
                    daily_usd_micros INTEGER NOT NULL,
                    last_epoch_s INTEGER NOT NULL DEFAULT 0,
                    frozen INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS budget_requests (
                    request_digest TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    minute_bucket INTEGER NOT NULL,
                    day_bucket INTEGER NOT NULL,
                    reserved_tokens INTEGER NOT NULL,
                    reserved_usd_micros INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'reserved',
                    used_tokens INTEGER,
                    used_usd_micros INTEGER
                );
                CREATE INDEX IF NOT EXISTS budget_minute_idx
                    ON budget_requests(minute_bucket);
                CREATE INDEX IF NOT EXISTS budget_day_idx
                    ON budget_requests(day_bucket);
            """)
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT OR IGNORE INTO budget_settings "
                    "(singleton, rpm, tpm, daily_usd_micros) VALUES (1, ?, ?, ?)",
                    (caps.requests_per_minute, caps.tokens_per_minute,
                     caps.usd_micros_per_utc_day),
                )
                stored = db.execute(
                    "SELECT rpm, tpm, daily_usd_micros FROM budget_settings WHERE singleton=1"
                ).fetchone()
                if stored != (
                    caps.requests_per_minute, caps.tokens_per_minute,
                    caps.usd_micros_per_utc_day,
                ):
                    raise ValueError("budget caps mismatch: explicitly migrate policy")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _connect(self) -> sqlite3.Connection:
        # The paid-provider ledger may not fail open on platforms where the
        # pathname/ownership guard or nofollow opening cannot be enforced.
        if (os.name != "posix" or not getattr(os, "O_NOFOLLOW", 0)
                or not getattr(os, "O_DIRECTORY", 0)):
            raise BudgetDenied("private POSIX ledger storage is required")
        if not Path(self.path).is_absolute():
            raise BudgetDenied("budget ledger path must be absolute")
        if os.name == "posix":
            _check_private_sqlite_path(self.path)
            flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_CLOEXEC", 0))
            try:
                fd = os.open(self.path, flags, 0o600)
            except FileExistsError:
                pass
            else:
                try:
                    # SQLite's WAL transaction durability does not guarantee
                    # that the new database inode was durably published.
                    os.fsync(fd)
                finally:
                    os.close(fd)
            _check_private_sqlite_path(self.path)
            # Sync the owner-private parent before permitting any reservation.
            # Reopen also retries a directory sync after an uncertain failure.
            parent_fd = os.open(
                str(Path(self.path).parent),
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA journal_mode=WAL")
            if os.name == "posix":
                _check_private_sqlite_path(self.path)
            return db
        except BaseException:
            db.close()
            raise

    @staticmethod
    def _digest(key: str) -> str:
        if not isinstance(key, str) or not key or len(key.encode("utf-8")) > 256:
            raise ValueError("idempotency key must be 1..256 UTF-8 bytes")
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def reserve(
        self, key: str, model: str, tokens: int, usd_micros: int,
        *, now_s: int | None = None,
    ) -> Admission:
        """Atomically reserve *maximum* tokens/cost before any model call."""
        digest = self._digest(key)
        if not isinstance(model, str) or not model or len(model) > 128:
            raise ValueError("model must be a nonempty identifier <=128 chars")
        if type(tokens) is not int or not 1 <= tokens <= 10**12:
            raise ValueError("tokens must be a positive bounded integer")
        if type(usd_micros) is not int or not 0 <= usd_micros <= 10**12:
            raise ValueError("usd_micros must be a nonnegative bounded integer")
        if now_s is None:
            now_s = int(time.time())
        if type(now_s) is not int or now_s < 0:
            raise ValueError("now_s must be a nonnegative integer epoch")

        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT last_epoch_s, frozen, rpm, tpm, daily_usd_micros "
                    "FROM budget_settings WHERE singleton=1"
                ).fetchone()
                if row is None or row[1]:
                    raise BudgetDenied("budget ledger missing or frozen")
                # SQLite settings are authoritative: changing the public caps
                # attribute must never increase allowance after initialization.
                persisted_caps = row[2:]
                if (type(self.caps) is not BudgetCaps or persisted_caps != (
                    self.caps.requests_per_minute,
                    self.caps.tokens_per_minute,
                    self.caps.usd_micros_per_utc_day,
                )):
                    raise BudgetDenied("budget caps mismatch: ledger policy is immutable")
                if now_s < row[0]:
                    raise BudgetDenied("clock regression: admission closed")
                existing = db.execute(
                    "SELECT model, reserved_tokens, reserved_usd_micros "
                    "FROM budget_requests WHERE request_digest=?", (digest,),
                ).fetchone()
                if existing is not None:
                    if existing != (model, tokens, usd_micros):
                        raise BudgetDenied("idempotency key reused with different request")
                    db.execute(
                        "UPDATE budget_settings SET last_epoch_s=? WHERE singleton=1",
                        (now_s,),
                    )
                    db.commit()
                    return Admission(digest, model, tokens, usd_micros, True)

                minute, day = now_s // 60, now_s // 86400
                count, total_tokens = db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(reserved_tokens), 0) "
                    "FROM budget_requests WHERE minute_bucket=?", (minute,),
                ).fetchone()
                (spent,) = db.execute(
                    "SELECT COALESCE(SUM(reserved_usd_micros), 0) "
                    "FROM budget_requests WHERE day_bucket=?", (day,),
                ).fetchone()
                if (
                    count >= persisted_caps[0]
                    or total_tokens + tokens > persisted_caps[1]
                    or spent + usd_micros > persisted_caps[2]
                ):
                    raise BudgetDenied("model request exceeds rate or daily cost cap")
                db.execute(
                    "INSERT INTO budget_requests "
                    "(request_digest, model, minute_bucket, day_bucket, "
                    "reserved_tokens, reserved_usd_micros) VALUES (?, ?, ?, ?, ?, ?)",
                    (digest, model, minute, day, tokens, usd_micros),
                )
                db.execute(
                    "UPDATE budget_settings SET last_epoch_s=? WHERE singleton=1",
                    (now_s,),
                )
                db.commit()
                return Admission(digest, model, tokens, usd_micros, False)
            except BaseException:
                db.rollback()
                raise

    def record_usage(self, key: str, tokens: int, usd_micros: int) -> None:
        """Record provider usage; an overrun freezes new admissions persistently."""
        digest = self._digest(key)
        if type(tokens) is not int or tokens < 0 or tokens > 10**12:
            raise ValueError("actual tokens must be a bounded integer")
        if type(usd_micros) is not int or usd_micros < 0 or usd_micros > 10**12:
            raise ValueError("actual cost must be a bounded integer")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT reserved_tokens, reserved_usd_micros, status, "
                    "used_tokens, used_usd_micros FROM budget_requests "
                    "WHERE request_digest=?", (digest,),
                ).fetchone()
                if row is None:
                    raise BudgetDenied("unknown reservation")
                rt, rc, state, old_tokens, old_cost = row
                if state != "reserved":
                    if (old_tokens, old_cost) != (tokens, usd_micros):
                        # Persist the freeze before raising, otherwise a
                        # contradictory provider receipt fails open.
                        db.execute(
                            "UPDATE budget_settings SET frozen=1 WHERE singleton=1"
                        )
                        db.commit()
                        raise BudgetDenied(
                            "conflicting provider usage receipt; ledger frozen"
                        )
                    db.commit()
                    if state == "overrun":
                        raise BudgetDenied("provider exceeded reservation; ledger frozen")
                    return
                overrun = tokens > rt or usd_micros > rc
                db.execute(
                    "UPDATE budget_requests SET status=?, used_tokens=?, "
                    "used_usd_micros=? WHERE request_digest=?",
                    ("overrun" if overrun else "complete", tokens, usd_micros, digest),
                )
                if overrun:
                    db.execute(
                        "UPDATE budget_settings SET frozen=1 WHERE singleton=1"
                    )
                db.commit()
                if overrun:
                    raise BudgetDenied("provider exceeded reservation; ledger frozen")
            except BaseException:
                if db.in_transaction:
                    db.rollback()
                raise
