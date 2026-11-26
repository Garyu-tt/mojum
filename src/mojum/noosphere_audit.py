"""Strict offline, read-only integrity audit for a local Noosphere journal.

This checks one local SQLite chain against its own data and, optionally, an
independently obtained expected head. It does not authenticate an unanchored
journal, export memory, repair the database, or open a writable connection.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3

from .noosphere import LocalNoosphere, NoosphereError

REPORT_SCHEMA = "mojum.noosphere.audit/1"
MAX_EVENT_WIRE_BYTES = 128_000
MAX_PATH_BYTES = 4096
_HEX_HEAD = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class NoosphereAuditError(ValueError):
    """The journal or the requested audit is not admissible."""


def _admit_path(db_path: str | Path) -> Path:
    """Reject nonexistent paths, symlinked ancestors, URI and traversal tricks.

    This is a local operator safety check, not a defense against a concurrent
    hostile process swapping filesystem nodes after validation.
    """
    if not isinstance(db_path, (str, Path)):
        raise NoosphereAuditError("invalid database path")
    raw = os.fspath(db_path)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise NoosphereAuditError("invalid database path")
    try:
        if len(raw.encode("utf-8")) > MAX_PATH_BYTES:
            raise NoosphereAuditError("database path exceeds byte budget")
    except UnicodeEncodeError as exc:
        raise NoosphereAuditError("invalid database path") from exc
    relative = Path(raw)
    if ".." in relative.parts:
        raise NoosphereAuditError("parent traversal is not admitted")
    absolute = Path(os.path.abspath(relative))
    for part in (absolute, *absolute.parents):
        if part.is_symlink():
            raise NoosphereAuditError("database path contains a symlink")
    if not absolute.is_file():
        raise NoosphereAuditError("existing regular database file required")
    return absolute


def audit_noosphere(
    db_path: str | Path,
    agent_id: str,
    *,
    expected_head: str | None = None,
) -> dict[str, object]:
    """Verify every canonical event without returning their contents.

    An explicit BEGIN pins one read snapshot for both the wire-size preflight
    and chain verification, even if a separate owner concurrently appends.
    Without an external head, the result proves *internal* consistency only.
    """
    if expected_head is not None and (
        type(expected_head) is not str or _HEX_HEAD.fullmatch(expected_head) is None
    ):
        raise NoosphereAuditError("expected_head must be a lowercase SHA-256 hex digest")
    try:
        path = _admit_path(db_path)
        with LocalNoosphere(path, agent_id, read_only=True) as journal:
            journal._db.execute("BEGIN")
            try:
                # Corrupt foreign rows can be arbitrarily large. Reject them
                # *before* Python JSON decoding, not after allocating a tree.
                too_large = journal._db.execute(
                    "SELECT 1 FROM events "
                    "WHERE length(CAST(wire_json AS BLOB)) > ? LIMIT 1",
                    (MAX_EVENT_WIRE_BYTES,),
                ).fetchone()
                if too_large is not None:
                    raise NoosphereAuditError("journal wire size exceeds event budget")
                verified = journal.verify_chain(
                    external_expected_head=expected_head, strict_wire=True
                )
            finally:
                journal._db.execute("ROLLBACK")
    except (NoosphereError, NoosphereAuditError, sqlite3.Error, OSError,
            ValueError, TypeError) as exc:
        raise NoosphereAuditError("journal_verification_failed") from exc
    return {
        "schema": REPORT_SCHEMA,
        "status": "PASS",
        "count": verified["count"],
        "head": verified["head"],
        "expected_head_verified": expected_head is not None,
        "trust": (
            "EXTERNAL_HEAD_MATCHED_NOT_A_SIGNATURE"
            if expected_head is not None
            else "LOCAL_CHAIN_ONLY_NOT_AUTHENTICATED"
        ),
        "contents": "NOT_EXPORTED",
        "writes": "NOT_PERFORMED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Noosphere integrity audit; no event contents exported"
    )
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--expected-head", default=None,
                        help="64 lowercase hex characters from an independent trusted record")
    args = parser.parse_args(argv)
    try:
        report = audit_noosphere(
            args.db, args.agent_id, expected_head=args.expected_head
        )
    except NoosphereAuditError:
        # Deliberately do not print errors from the database, exceptions,
        # paths, owners or malformed PRIVATE payloads.
        report = {"schema": REPORT_SCHEMA, "status": "FAIL",
                  "error": "journal_verification_failed"}
        code = 1
    else:
        code = 0
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
