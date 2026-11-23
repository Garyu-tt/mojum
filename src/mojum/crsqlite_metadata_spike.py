"""Opt-in, in-memory CR-SQLite comparison for PUBLIC collaborative metadata.

No network, Noosphere, canonical journal, installation or actuator access.
Running native extension code requires an explicit executable-path SHA-256 pin.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat

SCHEMA = "mojum.crsqlite.public-metadata-changes/1"
TABLE = "collab_notes"
MAX_CHANGES = 256
MAX_WIRE = 65536
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:-]{0,63}\Z", re.ASCII)
_COLUMNS = ('table', 'pk', 'cid', 'val', 'col_version', 'db_version', 'site_id', 'cl', 'seq')
_SELECT = ('SELECT "table", "pk", "cid", "val", "col_version", "db_version", '
           '"site_id", "cl", "seq" FROM crsql_changes WHERE db_version > 0 '
           'ORDER BY db_version, seq LIMIT ?')
_INSERT = ('INSERT INTO crsql_changes '
           '("table", "pk", "cid", "val", "col_version", "db_version", '
           '"site_id", "cl", "seq") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)')


def _positive_int(value, *, allow_zero=False):
    if type(value) is not int or value < (0 if allow_zero else 1) or value > 2**53 - 1:
        raise ValueError("invalid CR-SQLite version/counter")
    return value


def _public_label(value):
    if type(value) is not str or not _LABEL.fullmatch(value):
        raise ValueError("only bounded PUBLIC ASCII metadata labels are permitted")
    return value


def _binary(value, *, exact=None, maximum=256):
    if not isinstance(value, (bytes, memoryview)):
        raise ValueError("invalid CR-SQLite binary column")
    value = bytes(value)
    if not value or len(value) > maximum or (exact is not None and len(value) != exact):
        raise ValueError("invalid CR-SQLite binary length")
    return value


def _encode_change(row):
    if not isinstance(row, (tuple, list)) or len(row) != 9:
        raise ValueError("invalid CR-SQLite change shape")
    table, pk, cid, val, col_version, db_version, site_id, cl, seq = row
    if table != TABLE or cid != "value":
        raise ValueError("unapproved replicated table or column")
    return {
        "table": TABLE,
        "pk": base64.b64encode(_binary(pk)).decode("ascii"),
        "cid": "value",
        "val": _public_label(val),
        "col_version": _positive_int(col_version),
        "db_version": _positive_int(db_version),
        "site_id": base64.b64encode(_binary(site_id, exact=16)).decode("ascii"),
        "cl": _positive_int(cl, allow_zero=True),
        "seq": _positive_int(seq, allow_zero=True),
    }


def _decode_blob(encoded, exact=None):
    if type(encoded) is not str or len(encoded) > 344:
        raise ValueError("invalid base64 field")
    try:
        blob = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("invalid base64 field") from exc
    if base64.b64encode(blob).decode("ascii") != encoded:
        raise ValueError("noncanonical base64")
    return _binary(blob, exact=exact)


def _decode_change(record):
    if type(record) is not dict or set(record) != set(_COLUMNS):
        raise ValueError("unexpected CR-SQLite change fields")
    if record["table"] != TABLE or record["cid"] != "value":
        raise ValueError("unapproved replicated table or column")
    return (
        TABLE, _decode_blob(record["pk"]), "value", _public_label(record["val"]),
        _positive_int(record["col_version"]), _positive_int(record["db_version"]),
        _decode_blob(record["site_id"], exact=16),
        _positive_int(record["cl"], allow_zero=True),
        _positive_int(record["seq"], allow_zero=True),
    )


def _unique_keys(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate JSON field")
        data[key] = value
    return data


def pack_changes(rows):
    """Bounded non-authenticated test envelope, never a network security token."""
    rows = list(rows)
    if len(rows) > MAX_CHANGES:
        raise ValueError("too many changes")
    payload = json.dumps({"schema": SCHEMA, "changes": [_encode_change(row) for row in rows]},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(payload) > MAX_WIRE:
        raise ValueError("oversized changeset")
    return payload


def unpack_changes(payload):
    if type(payload) is not bytes or len(payload) > MAX_WIRE:
        raise ValueError("invalid changeset size/type")
    try:
        data = json.loads(payload, object_pairs_hook=_unique_keys)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid changeset JSON") from exc
    if type(data) is not dict or set(data) != {"schema", "changes"} or data["schema"] != SCHEMA:
        raise ValueError("invalid changeset schema")
    changes = data["changes"]
    if type(changes) is not list or len(changes) > MAX_CHANGES:
        raise ValueError("invalid changeset list")
    return tuple(_decode_change(change) for change in changes)


def _require_memory(conn):
    # A future caller must not reuse this API against canonical/PRIVATE SQLite.
    databases = conn.execute("PRAGMA database_list").fetchall()
    if not databases or any(len(row) != 3 or row[2] != "" for row in databases):
        raise ValueError("CR-SQLite spike accepts only isolated in-memory replicas")


def export_changes(conn):
    _require_memory(conn)
    # Read one excess row to fail closed on incomplete/truncated exports.
    rows = conn.execute(_SELECT, (MAX_CHANGES + 1,)).fetchall()
    return pack_changes(rows)


def apply_changes(conn, payload):
    """Validate the entire batch *before* any write; native CRR handles dedup."""
    rows = unpack_changes(payload)
    _require_memory(conn)
    with conn:
        for row in rows:
            conn.execute(_INSERT, row)
    return len(rows)


def checked_extension(path, sha256):
    """Explicit digest pin and no symlinks. Does not download or install binaries."""
    if type(sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise ValueError("explicit lowercase SHA-256 pin required")
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("extension must be an absolute non-symlink path")
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size > 128 * 1024 * 1024:
        raise ValueError("unsafe extension file")
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat(follow_symlinks=False)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("extension changed while hashing")
    if digest.hexdigest() != sha256:
        raise ValueError("extension SHA-256 mismatch")
    return path


def _open_replica(path):
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.enable_load_extension(True)
        try:
            conn.load_extension(str(path))
        finally:
            conn.enable_load_extension(False)
        # Release v0.16.2+ publishes this function. Older incompatible builds fail closed.
        sha = conn.execute("SELECT crsql_sha()").fetchone()[0]
        if type(sha) is not str or not re.fullmatch(r"[0-9a-f]{7,64}", sha):
            raise ValueError("unsupported CR-SQLite extension build")
        with conn:
            conn.execute("CREATE TABLE collab_notes (id TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)")
            conn.execute("SELECT crsql_as_crr(?)", (TABLE,))
        return conn
    except BaseException:
        conn.close()
        raise


def run_probe(extension, sha256):
    """Two isolated in-memory peers; synthetic PUBLIC labels, no persistent DB."""
    path = checked_extension(extension, sha256)
    conns = []
    try:
        a = _open_replica(path)
        conns.append(a)
        b = _open_replica(path)
        conns.append(b)
        with a:
            a.executemany("INSERT INTO collab_notes (id,value) VALUES (?,?)",
                          (("shared", "alpha"), ("left", "one")))
        with b:
            b.executemany("INSERT INTO collab_notes (id,value) VALUES (?,?)",
                          (("shared", "beta"), ("right", "two")))
        left, right = export_changes(a), export_changes(b)
        # Apply in differing orders and duplicate complete batches: a failure must be visible.
        apply_changes(a, right)
        apply_changes(b, left)
        apply_changes(a, right)
        apply_changes(b, left)
        for _ in range(2):
            new_left, new_right = export_changes(a), export_changes(b)
            apply_changes(a, new_right)
            apply_changes(b, new_left)
        def snapshot(conn):
            return tuple(conn.execute("SELECT id,value FROM collab_notes ORDER BY id"))
        l, r = snapshot(a), snapshot(b)
        if l != r or {x[0] for x in l} != {"left", "right", "shared"}:
            raise RuntimeError("CR-SQLite replica convergence not demonstrated")
        return {"converged": True, "replicas": 2, "rows": len(l),
                "winner": dict(l)["shared"], "storage": "in-memory",
                "authority": "non-authoritative-public-metadata-only"}
    finally:
        for conn in reversed(conns):
            try:
                conn.execute("SELECT crsql_finalize()")
            finally:
                conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Opt-in isolated CR-SQLite metadata probe")
    parser.add_argument("--execute", action="store_true", help="explicitly permit loading native code")
    parser.add_argument("--extension", type=Path, help="absolute path to local crsqlite extension")
    parser.add_argument("--sha256", help="trusted SHA-256 of extension bytes")
    args = parser.parse_args(argv)
    if not (args.execute and args.extension and args.sha256):
        parser.error("requires --execute, --extension and --sha256; never auto-loads")
    try:
        result = run_probe(args.extension, args.sha256)
    except (OSError, ValueError, sqlite3.Error, RuntimeError) as exc:
        parser.exit(2, f"CR-SQLite probe failed: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
