"""Read-only, metadata-only SQLite schema fingerprint for offline diagnostics.

Never reads application rows or prints schema SQL, table names, or file paths.
This fingerprint is NOT an integrity proof for journal content or a backup.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

SCHEMA = "mojum.sqlite.schema-fingerprint/1"
MAX_BYTES = 1024 * 1024 * 1024
MAX_ENTRIES = 4096
MAX_TEXT = 65536
MAX_TOTAL_TEXT = 4 * 1024 * 1024


class SchemaAuditError(ValueError):
    """An unsafe or unsupported schema inspection."""


def _sidecar_free_wal_snapshot(target: Path) -> bool:
    # SQLite header bytes 18 and 19 both equal 2 for WAL databases.
    with target.open("rb", buffering=0) as source:
        header = source.read(20)
    if not header.startswith(b"SQLite format 3\x00") or header[18:20] != b"\x02\x02":
        return False

    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = target.with_name(target.name + suffix)
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        return False
    return True


def fingerprint(path: str | os.PathLike[str]) -> dict[str, object]:
    """Inspect only sqlite_schema through a read-only SQLite connection.

    The caller must supply a stable database in a trusted directory. A
    sidecar-free WAL snapshot is opened without SQLite locking. The pathname
    lstat check is not an fd-bound no-follow open.
    """
    target = Path(path)
    try:
        info = target.lstat()
    except OSError as exc:
        raise SchemaAuditError("database is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
        raise SchemaAuditError("database must be a bounded regular file")
    # Avoid SQLite URI parsing ambiguity by using a percent-encoded absolute path.
    from urllib.parse import quote

    uri = "file:" + quote(str(target.absolute()), safe="/") + "?mode=ro"
    try:
        # SQLite can create both WAL sidecars even in mode=ro. Only disable
        # its WAL machinery when the stable snapshot has no sidecars to read.
        if _sidecar_free_wal_snapshot(target):
            uri += "&immutable=1"
        # sqlite3.Connection's context manager controls transactions, not
        # lifetime. closing() guarantees release on success and exceptions.
        with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as db:
            with db:
                db.execute("PRAGMA query_only=ON")
                db.set_progress_handler(lambda: 1, 100000)
                rows = db.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_schema "
                    "ORDER BY type COLLATE BINARY,name COLLATE BINARY,tbl_name COLLATE BINARY"
                )
                digest = hashlib.sha256()
                count = 0
                total_text = 0
                for record in rows:
                    count += 1
                    if count > MAX_ENTRIES:
                        raise SchemaAuditError("schema entry budget exceeded")
                    for value in record:
                        if value is None:
                            continue
                        # Character count is a cheap lower bound on UTF-8 size;
                        # reject before allocating an encoded copy of huge SQL.
                        if type(value) is not str or len(value) > MAX_TEXT:
                            raise SchemaAuditError("invalid or oversized schema metadata")
                        text_size = len(value.encode("utf-8"))
                        if text_size > MAX_TEXT:
                            raise SchemaAuditError("invalid or oversized schema metadata")
                        total_text += text_size
                        if total_text > MAX_TOTAL_TEXT:
                            raise SchemaAuditError("aggregate schema text budget exceeded")
                    encoded = json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode()
                    digest.update(len(encoded).to_bytes(4, "big"))
                    digest.update(encoded)
                if count == 0:
                    raise SchemaAuditError("empty schema is not an accepted Mojum journal")
                return {"schema": SCHEMA, "entries": count, "sha256": digest.hexdigest()}
    except (sqlite3.Error, OSError, UnicodeError, OverflowError) as exc:
        raise SchemaAuditError("SQLite schema inspection failed") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only SQLite schema fingerprint (no row data)")
    parser.add_argument("database")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(fingerprint(args.database), sort_keys=True))
        return 0
    except SchemaAuditError as exc:
        print(f"schema audit rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
