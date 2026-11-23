"""Opt-in authenticated receipts for *external* Mojum agent runs (#717).

This module is an offline ingestion primitive, NOT an HTTP server, Cezar worker
scheduler, live process probe, or proof that a scheduled automation ran.
Every receipt is authenticated with an out-of-band per-source/agent HMAC key.
Only server-observed time determines heartbeat liveness. All persisted records
are PRIVATE operational metadata; never expose the database publicly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Mapping

SCHEMA = "mojum.external-agent-receipt/1"
KINDS = frozenset({"agent.started", "agent.heartbeat", "agent.finished", "agent.failed"})
TERMINAL_KINDS = frozenset({"agent.finished", "agent.failed"})
STATUS_CODES = frozenset({"none", "completed", "failed", "cancelled", "rate_limited", "unreachable"})
_FIELDS = frozenset({
    "schema", "source", "agent_id", "host", "run_id", "session_id", "repo",
    "provider", "kind", "seq", "timestamp_ms", "ttl_ms", "issue", "pr",
    "sha", "status_code",
})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_SOURCE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
_REPO = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}\Z", re.ASCII)
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z", re.ASCII)
_HEX_SIGNATURE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_IDENTIFY = ("source", "agent_id", "host", "run_id", "session_id", "repo", "provider")
MAX_RECEIPT_BYTES = 2048
MAX_CLOCK_SKEW_MS = 5 * 60 * 1000
MAX_MILLISECONDS = 2**63 - 1


class ReceiptError(ValueError):
    """Untrusted receipt, invalid transition, or conflicting replay."""


def _int(value: object, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ReceiptError(f"invalid {label}")
    return value


def _id(value: object, label: str, pattern: re.Pattern[str] = _ID) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ReceiptError(f"invalid {label}")
    return value


def validate_receipt(event: object) -> dict:
    """Return a strict, JSON-serializable copy (no extra diagnostic text)."""
    if type(event) is not dict or set(event) != _FIELDS:
        raise ReceiptError("invalid receipt fields")
    if event["schema"] != SCHEMA:
        raise ReceiptError("unsupported receipt schema")
    _id(event["source"], "source", _SOURCE)
    for name in ("agent_id", "host", "run_id", "session_id", "provider"):
        _id(event[name], name)
    _id(event["repo"], "repo", _REPO)
    if type(event["kind"]) is not str or event["kind"] not in KINDS:
        raise ReceiptError("unknown event kind")
    if type(event["status_code"]) is not str or event["status_code"] not in STATUS_CODES:
        raise ReceiptError("invalid redacted status code")
    if event["kind"] in {"agent.started", "agent.heartbeat"} and event["status_code"] != "none":
        raise ReceiptError("nonterminal event must not invent a diagnosis")
    if event["kind"] == "agent.finished" and event["status_code"] != "completed":
        raise ReceiptError("finished event requires completed status")
    if event["kind"] == "agent.failed" and event["status_code"] not in STATUS_CODES - {"none", "completed"}:
        raise ReceiptError("failed event requires a redacted failure code")
    _int(event["seq"], "seq", 1, 1_000_000)
    _int(event["timestamp_ms"], "timestamp_ms", 0, MAX_MILLISECONDS)
    _int(event["ttl_ms"], "ttl_ms", 1_000, 300_000)
    for name in ("issue", "pr"):
        if event[name] is not None:
            _int(event[name], name, 1, 2**31 - 1)
    if event["sha"] is not None:
        _id(event["sha"], "sha", _SHA)
    # A shallow copy suffices: strictly validated values are scalar primitives.
    result = dict(event)
    if len(_canonical(result)) > MAX_RECEIPT_BYTES:
        raise ReceiptError("receipt exceeds size limit")
    return result


def _canonical(event: dict) -> bytes:
    try:
        return json.dumps(event, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ReceiptError("invalid JSON payload") from exc


def _secret(secret: object) -> bytes:
    if type(secret) is not bytes or len(secret) < 32:
        raise ReceiptError("HMAC key must be at least 32 bytes")
    return secret


def sign_receipt(event: dict, secret: bytes) -> str:
    """Test/producer helper; caller must protect its signing key out of band."""
    payload = _canonical(validate_receipt(event))
    return hmac.new(_secret(secret), payload, hashlib.sha256).hexdigest()


def _now(value: int | None) -> int:
    if value is None:
        value = time.time_ns() // 1_000_000
    return _int(value, "trusted server time", 0, MAX_MILLISECONDS)


def _open_private_sqlite(path: str | os.PathLike[str]) -> sqlite3.Connection:
    p = Path(path)
    if not p.is_absolute():
        raise ReceiptError("SQLite path must be absolute")
    if not p.parent.is_dir() or p.parent.is_symlink():
        raise ReceiptError("SQLite parent must be a trusted existing directory")
    try:
        mode = p.lstat().st_mode
    except FileNotFoundError:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(p, flags, 0o600)
        except FileExistsError as exc:
            raise ReceiptError("path changed while initializing SQLite") from exc
        else:
            os.close(fd)
        mode = p.lstat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise ReceiptError("SQLite database must be a private regular file (0600)")
    if hasattr(os, "geteuid") and p.stat().st_uid != os.geteuid():
        raise ReceiptError("SQLite database must belong to current OS user")
    # Never persist signing keys. Restrict SQLite to a trusted local directory;
    # no symlink races from adversarial filesystem users are supported.
    conn = sqlite3.connect(str(p), timeout=5, isolation_level=None)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""CREATE TABLE IF NOT EXISTS receipts (
        source TEXT NOT NULL, agent_id TEXT NOT NULL, run_id TEXT NOT NULL,
        seq INTEGER NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
        observed_ms INTEGER NOT NULL,
        PRIMARY KEY (source, agent_id, run_id, seq)
    )""")
    return conn


class ReceiptJournal:
    """Readbackable, fail-closed private SQLite receipt journal.

    `trusted_keys` maps (source, agent_id, repo) -> bytes of 32+ random secret.
    It is not written to SQLite; source/agent/repo must be enrolled externally.
    """

    def __init__(self, path: str | os.PathLike[str],
                 trusted_keys: Mapping[tuple[str, str, str], bytes]):
        if not isinstance(trusted_keys, Mapping) or not trusted_keys:
            raise ReceiptError("trusted key map required")
        self._keys = {}
        for identity, secret in trusted_keys.items():
            if type(identity) is not tuple or len(identity) != 3:
                raise ReceiptError("invalid trusted identity")
            _id(identity[0], "source", _SOURCE)
            _id(identity[1], "agent_id")
            _id(identity[2], "repo", _REPO)
            self._keys[identity] = _secret(secret)
        self._conn = _open_private_sqlite(path)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ReceiptJournal":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def ingest(self, event: dict, signature: str, *, observed_ms: int | None = None) -> bool:
        """Verify and append; False is an exact duplicate, never new liveness.

        Reject new receipts with gaps, wrong owner, past terminal state,
        conflicting sequence or clock skew. Always authenticate duplicates.
        """
        value = validate_receipt(event)
        key = self._keys.get((value["source"], value["agent_id"], value["repo"]))
        if key is None:
            raise ReceiptError("unknown authenticated identity")
        if type(signature) is not str or _HEX_SIGNATURE.fullmatch(signature) is None:
            raise ReceiptError("invalid signature")
        raw = _canonical(value)
        expected = hmac.new(key, raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ReceiptError("signature verification failed")
        digest = hashlib.sha256(raw).hexdigest()
        observed = _now(observed_ms)
        owner = (value["source"], value["agent_id"], value["run_id"])
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            same = self._conn.execute(
                "SELECT digest FROM receipts WHERE source=? AND agent_id=? AND run_id=? AND seq=?",
                (*owner, value["seq"])).fetchone()
            if same is not None:
                if not hmac.compare_digest(same[0], digest):
                    raise ReceiptError("conflicting sequence replay")
                self._conn.execute("COMMIT")
                return False
            previous = self._conn.execute(
                "SELECT seq, payload, observed_ms FROM receipts "
                "WHERE source=? AND agent_id=? AND run_id=? ORDER BY seq DESC LIMIT 1",
                owner).fetchone()
            if abs(value["timestamp_ms"] - observed) > MAX_CLOCK_SKEW_MS:
                raise ReceiptError("source timestamp outside server admission window")
            if previous is None:
                if value["seq"] != 1 or value["kind"] != "agent.started":
                    raise ReceiptError("first event must be seq=1 agent.started")
            else:
                old_seq, old_payload, old_observed = previous
                old = json.loads(old_payload)
                if old["kind"] in TERMINAL_KINDS:
                    raise ReceiptError("run already terminal")
                if value["seq"] != old_seq + 1:
                    raise ReceiptError("sequence gap or replay")
                if (value["timestamp_ms"] < old["timestamp_ms"]
                        or observed < old_observed):
                    raise ReceiptError("nonmonotonic time")
                if any(value[name] != old[name] for name in _IDENTIFY):
                    raise ReceiptError("run identity changed")
            self._conn.execute(
                "INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*owner, value["seq"], raw.decode("ascii"), digest, observed))
            self._conn.execute("COMMIT")
            return True
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def readback(self, *, observed_ms: int | None = None, limit: int = 1000) -> dict:
        """Bounded dashboard projection of *verified receipts only*.

        `truncated` is explicit so callers never treat a partial view as the
        complete fleet. Schedules and Telegram notifications are not receipts.
        """
        observed = _now(observed_ms)
        _int(limit, "readback limit", 1, 1000)
        rows = self._conn.execute(
            "SELECT DISTINCT source, agent_id, run_id FROM receipts "
            "ORDER BY source, agent_id, run_id LIMIT ?", (limit + 1,)).fetchall()
        return {"observed_ms": observed, "truncated": len(rows) > limit,
                "runs": [self.status(*identity, observed_ms=observed)
                         for identity in rows[:limit]]}

    def status(self, source: str, agent_id: str, run_id: str, *,
               observed_ms: int | None = None) -> dict:
        """Return verified latest state; absent runs remain **unknown**."""
        _id(source, "source", _SOURCE)
        _id(agent_id, "agent_id")
        _id(run_id, "run_id")
        observed = _now(observed_ms)
        row = self._conn.execute(
            "SELECT payload, observed_ms FROM receipts "
            "WHERE source=? AND agent_id=? AND run_id=? ORDER BY seq DESC LIMIT 1",
            (source, agent_id, run_id)).fetchone()
        if row is None:
            return {"source": source, "agent_id": agent_id, "run_id": run_id,
                    "state": "unknown", "reason": "no_authenticated_receipt"}
        event = json.loads(row[0])
        server_seen = row[1]
        if observed < server_seen:
            raise ReceiptError("query time before recorded server receipt")
        if event["kind"] == "agent.finished":
            state = "finished"
        elif event["kind"] == "agent.failed":
            state = "failed"
        elif observed - server_seen >= event["ttl_ms"]:
            state = "stale"
        else:
            state = "active"
        return {
            "source": source, "agent_id": agent_id, "run_id": run_id,
            "state": state, "kind": event["kind"], "seq": event["seq"],
            "observed_ms": server_seen, "age_ms": observed - server_seen,
            "ttl_ms": event["ttl_ms"], "repo": event["repo"],
            "host": event["host"], "provider": event["provider"],
            "issue": event["issue"], "pr": event["pr"], "sha": event["sha"],
            "status_code": event["status_code"],
        }
