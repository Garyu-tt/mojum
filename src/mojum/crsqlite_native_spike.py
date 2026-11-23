"""Opt-in CR-SQLite v0.16.3 local disposable two-peer native smoke (#759).

Untrusted native SQLite extensions are executable code. Never point this at
Noosphere / existing SQLite data. This does NOT create a secure OS sandbox.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

MAX_EXTENSION_BYTES = 64 * 1024 * 1024

# Executed only with a digest-pinned binary, in a separate subprocess and with
# fresh in-memory databases. API uses cr-sqlite >=0.16.1 site_id semantics.
_NATIVE = r'''
import json
import sqlite3
import sys

def peer(path):
    db = sqlite3.connect(":memory:")
    db.enable_load_extension(True)
    try:
        db.load_extension(path)
    finally:
        db.enable_load_extension(False)
    db.execute("CREATE TABLE ui_meta (id TEXT PRIMARY KEY NOT NULL, x INTEGER NOT NULL)")
    db.execute("SELECT crsql_as_crr('ui_meta')")
    return db

a = peer(sys.argv[1])
b = peer(sys.argv[1])
try:
    # Deliberately conflicting edits to the same synthetic primary key:
    # convergence must hold even if the winning value is implementation-defined.
    a.execute("INSERT INTO ui_meta (id, x) VALUES ('v1', 10)")
    a.execute("INSERT INTO ui_meta (id, x) VALUES ('shared', 30)")
    b.execute("INSERT INTO ui_meta (id, x) VALUES ('v2', 20)")
    b.execute("INSERT INTO ui_meta (id, x) VALUES ('shared', 40)")
    a.commit()
    b.commit()
    fields = '"table","pk","cid","val","col_version","db_version","site_id","cl","seq"'
    query = "SELECT " + fields + " FROM crsql_changes WHERE site_id = crsql_site_id()"
    from_a, from_b = a.execute(query).fetchall(), b.execute(query).fetchall()
    assert from_a and from_b
    insert = "INSERT INTO crsql_changes VALUES (?,?,?,?,?,?,?,?,?)"
    a.executemany(insert, from_b)
    b.executemany(insert, from_a)
    a.commit()
    b.commit()
    actual_a = a.execute("SELECT id,x FROM ui_meta ORDER BY id").fetchall()
    actual_b = b.execute("SELECT id,x FROM ui_meta ORDER BY id").fetchall()
    assert actual_a == actual_b, "native replica convergence failure"
    assert len(actual_a) == 3, "missing native replicated records"
    values = dict(actual_a)
    assert values["v1"] == 10 and values["v2"] == 20
    assert values["shared"] in (30, 40), "invalid conflict resolution"
    # Newer release exposes exact upstream build identity; do not assume a
    # user-provided filename is proof of version.
    upstream_sha = a.execute("SELECT crsql_sha()").fetchone()[0]
    print(json.dumps({"native_converged": True, "rows": 3,
                      "upstream_sha": str(upstream_sha)[:80]}))
finally:
    for db in (a, b):
        try:
            db.execute("SELECT crsql_finalize()")
        finally:
            db.close()
'''


def run_native_probe(extension: str, expected_sha256: str, *, timeout_s: int = 15) -> dict:
    """Opt-in, audited extension ONLY; no network, journals, or writable paths.

    This child process is a *failure isolation boundary*, NOT a security
    sandbox. Native code can execute arbitrary OS operations under user rights.
    """
    if not isinstance(extension, str) or not os.path.isabs(extension):
        raise ValueError("extension must be an absolute path")
    if not isinstance(expected_sha256, str) or re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256, re.ASCII
    ) is None:
        raise ValueError("expected_sha256 must be a lowercase 64-char hex digest")
    if type(timeout_s) is not int or not 1 <= timeout_s <= 30:
        raise ValueError("timeout must be an integer between 1 and 30 s")
    source = Path(extension)
    if source.is_symlink() or source.name not in (
        "crsqlite.so", "crsqlite.dll", "crsqlite.dylib"
    ):
        raise ValueError("requires a pinned, explicitly named native extension")
    info = source.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_EXTENSION_BYTES:
        raise ValueError("invalid extension size or type")
    with source.open("rb") as f:
        data = f.read(MAX_EXTENSION_BYTES + 1)
    if len(data) != info.st_size or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("extension digest mismatch")
    with tempfile.TemporaryDirectory(prefix="mojum-crsqlite-probe-") as work:
        # Copy pinned bytes into a private disposable path to prevent changes
        # between verification and execution of the native extension.
        local = Path(work) / source.name
        local.write_bytes(data)
        local.chmod(0o600)
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-c", _NATIVE, str(local)],
                cwd=work, env={"PYTHONNOUSERSITE": "1"},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout_s, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("native extension probe timed out") from exc
        if result.returncode != 0 or len(result.stdout) > 4096:
            # Never return raw native stderr; it may reveal local paths.
            raise RuntimeError("native extension probe failed")
        try:
            report = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError("invalid native probe result") from exc
        if not isinstance(report, dict) or report.get("native_converged") is not True:
            raise RuntimeError("missing convergence proof")
        return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Explicit trusted cr-sqlite 0.16.3 fixture only")
    parser.add_argument("--extension", required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(run_native_probe(args.extension, args.sha256), sort_keys=True))
