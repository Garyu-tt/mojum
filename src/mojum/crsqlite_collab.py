"""Isolated, opt-in PUBLIC SYNTHETIC collaborative metadata CR-SQLite spike.

NOT a Noosphere, lease, policy, identity, or canonical Graph IR storage backend.
The stdlib reducer is a deterministic test oracle, NOT cr-sqlite's merge engine.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

SCHEMA = "mojum.collab-metadata-operation/1"
AUDIENCE = "PUBLIC_SYNTHETIC"
UPSTREAM_VERSION = "v0.16.3"
UPSTREAM_RELEASE_SHA_PREFIX = "0d62b52"
MAX_OPS = 128
MAX_EXTENSION_BYTES = 32 * 1024 * 1024
_KEY = re.compile(r"(?:layout|label|note)\.[a-z][a-z0-9_-]{0,39}\Z", re.ASCII)
_ID = re.compile(r"[a-z][a-z0-9_-]{0,39}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_FIELDS = frozenset({"schema", "audience", "workspace", "replica", "counter", "kind", "key", "value"})
_CHANGE_SQL = ('SELECT "table", "pk", "cid", "val", "col_version", "db_version", '
               '"site_id", "cl", "seq" FROM crsql_changes')
_INSERT_CHANGE_SQL = ("INSERT INTO crsql_changes (\"table\", \"pk\", \"cid\", \"val\", "
                      "\"col_version\", \"db_version\", \"site_id\", \"cl\", \"seq\") "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")


class MetadataBoundaryError(ValueError):
    """Input is outside the opt-in public synthetic metadata boundary."""


def validate_operation(value):
    """Reject unknown/private fields and return a bounded detached operation."""
    if type(value) is not dict or set(value) != _FIELDS:
        raise MetadataBoundaryError("unexpected metadata operation fields")
    if (type(value["schema"]) is not str or value["schema"] != SCHEMA or
            type(value["audience"]) is not str or value["audience"] != AUDIENCE):
        raise MetadataBoundaryError("unsupported schema or non-public synthetic audience")
    if (type(value["workspace"]) is not str or
            not value["workspace"].startswith("demo_") or
            _ID.fullmatch(value["workspace"]) is None):
        raise MetadataBoundaryError("only bounded demo_ workspaces permitted")
    if type(value["replica"]) is not str or _ID.fullmatch(value["replica"]) is None:
        raise MetadataBoundaryError("invalid replica label (not a host identity)")
    if type(value["counter"]) is not int or not 1 <= value["counter"] <= 2**31 - 1:
        raise MetadataBoundaryError("invalid bounded logical counter")
    if type(value["key"]) is not str or _KEY.fullmatch(value["key"]) is None:
        raise MetadataBoundaryError("metadata key restricted to layout/label/note namespace")
    if type(value["kind"]) is not str or value["kind"] not in ("put", "remove"):
        raise MetadataBoundaryError("invalid metadata operation kind")
    if value["kind"] == "remove":
        if value["value"] is not None:
            raise MetadataBoundaryError("removal must carry null value")
    else:
        v = value["value"]
        if (type(v) is not str or not 1 <= len(v) <= 256 or
                any(ord(ch) < 32 or ord(ch) > 126 for ch in v)):
            raise MetadataBoundaryError("value must be bounded printable ASCII")
    return dict(value)


def _checked_batch(ops):
    if type(ops) not in (list, tuple) or len(ops) > MAX_OPS:
        raise MetadataBoundaryError("operation batch exceeds hard limit")
    checked = [validate_operation(op) for op in ops]
    scopes = {op["workspace"] for op in checked}
    if len(scopes) > 1:
        raise MetadataBoundaryError("cross-workspace merge forbidden")
    seen = {}
    for op in checked:
        key = (op["replica"], op["counter"])
        if key in seen and seen[key] != op:
            raise MetadataBoundaryError("replica/counter equivocation")
        seen[key] = op
    return checked


def reduce_fixture(ops):
    """Deterministic LWW *oracle* for offline input/failure tests, not CR-SQLite.

    Duplicate retransmissions are idempotent. Higher (counter, replica)
    wins for each key. Tombstones prevent removal resurrection.
    """
    checked = _checked_batch(ops)
    winners = {}
    for op in checked:
        stamp = (op["counter"], op["replica"])
        key = op["key"]
        if key not in winners or stamp > winners[key][0]:
            winners[key] = (stamp, op["kind"], op["value"])
    projected = {key: winners[key][2] for key in sorted(winners)
                 if winners[key][1] == "put"}
    encoded = json.dumps(projected, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {"schema": "mojum.collab-metadata-projection/1",
            "audience": AUDIENCE, "entries": projected,
            "sha256": hashlib.sha256(encoded.encode("ascii")).hexdigest()}


def _read_pinned_extension(path, expected_sha256):
    if type(path) is not str or not os.path.isabs(path) or not path:
        raise MetadataBoundaryError("explicit absolute extension path required")
    if type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        raise MetadataBoundaryError("expected full SHA-256 required")
    p = Path(path)
    try:
        info = p.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or
                not 0 < info.st_size <= MAX_EXTENSION_BYTES):
            raise MetadataBoundaryError("unsafe extension inode, permissions or size")
        # An owner may still swap a pathname after hashing. An OS sandbox is mandatory.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                    or opened.st_size > MAX_EXTENSION_BYTES):
                raise MetadataBoundaryError("extension changed during preflight")
            raw = bytearray()
            while len(raw) <= MAX_EXTENSION_BYTES:
                chunk = os.read(fd, min(65536, MAX_EXTENSION_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) > MAX_EXTENSION_BYTES:
                raise MetadataBoundaryError("extension exceeds maximum bytes")
        finally:
            os.close(fd)
    except OSError as exc:
        raise MetadataBoundaryError("extension unavailable or unsafe") from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise MetadataBoundaryError("extension SHA-256 mismatch")
    return path


def _native_connection(path, sha256):
    """Create a throwaway in-memory connection; never load in authority DB."""
    verified = _read_pinned_extension(path, sha256)
    db = sqlite3.connect(":memory:")
    try:
        db.enable_load_extension(True)
        try:
            db.load_extension(verified)
        finally:
            db.enable_load_extension(False)
        version = db.execute("SELECT crsql_sha()").fetchone()
        if (not version or type(version[0]) is not str or
                not version[0].startswith(UPSTREAM_RELEASE_SHA_PREFIX)):
            raise MetadataBoundaryError("unexpected CR-SQLite release identity")
        db.execute("CREATE TABLE collab_meta (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)")
        db.execute("SELECT crsql_as_crr('collab_meta')")
        db.commit()
        return db
    except Exception:
        db.close()
        raise


def _apply_native(db, ops):
    for op in ops:
        if op["kind"] == "put":
            db.execute("INSERT INTO collab_meta (key, value) VALUES (?, ?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (op["key"], op["value"]))
        else:
            db.execute("DELETE FROM collab_meta WHERE key=?", (op["key"],))
    db.commit()


def _native_changes(db):
    rows = db.execute(_CHANGE_SQL + " LIMIT ?", (MAX_OPS * 16 + 1,)).fetchall()
    if len(rows) > MAX_OPS * 16:
        raise MetadataBoundaryError("native changes exceed bounded smoke limit")
    for row in rows:
        if len(row) != 9 or row[0] != "collab_meta":
            raise MetadataBoundaryError("unexpected CRR table in change stream")
    return rows


def _sync_native(from_db, to_db):
    rows = _native_changes(from_db)
    with to_db:
        to_db.executemany(_INSERT_CHANGE_SQL, rows)


def _checked_native_projection(state):
    """Reapply the exact PUBLIC_SYNTHETIC cell contract to native outputs."""
    if type(state) is not dict or len(state) > MAX_OPS * 2:
        raise MetadataBoundaryError("invalid native projection size or type")
    for key, value in state.items():
        if (type(key) is not str or _KEY.fullmatch(key) is None or
                type(value) is not str or not 1 <= len(value) <= 256 or
                any(ord(ch) < 32 or ord(ch) > 126 for ch in value)):
            raise MetadataBoundaryError("native output escaped public metadata boundary")
    return state


def native_partition_probe(extension_path, extension_sha256, left_ops, right_ops):
    """Opt-in real CR-SQLite two-replica test; no network or persistent databases.

    Requires manually provided SHA256-pinned v0.16.3 shared library.
    Run in disposable OS isolation: native extensions execute arbitrary code.
    """
    left = _checked_batch(left_ops)
    right = _checked_batch(right_ops)
    if not left or not right or left[0]["workspace"] != right[0]["workspace"]:
        raise MetadataBoundaryError("two nonempty same-workspace partitions required")
    if {x["replica"] for x in left} & {x["replica"] for x in right}:
        raise MetadataBoundaryError("partitions require distinct synthetic replica IDs")
    _checked_batch(left + right)
    lhs = _native_connection(extension_path, extension_sha256)
    try:
        rhs = _native_connection(extension_path, extension_sha256)
        try:
            _apply_native(lhs, left)
            _apply_native(rhs, right)
            _sync_native(lhs, rhs)
            _sync_native(rhs, lhs)
            state_l = dict(lhs.execute("SELECT key, value FROM collab_meta ORDER BY key").fetchall())
            state_r = dict(rhs.execute("SELECT key, value FROM collab_meta ORDER BY key").fetchall())
            if state_l != state_r:
                raise MetadataBoundaryError("native replicas failed convergence")
            _checked_native_projection(state_l)
            return {"schema": "mojum.collab-crsqlite-probe/1",
                    "upstream": UPSTREAM_VERSION, "converged": True,
                    "entries": state_l, "changes_left": len(_native_changes(lhs)),
                    "changes_right": len(_native_changes(rhs))}
        finally:
            try:
                rhs.execute("SELECT crsql_finalize()")
            finally:
                rhs.close()
    finally:
        try:
            lhs.execute("SELECT crsql_finalize()")
        finally:
            lhs.close()
