"""Opt-in, local-only Litestream v0.5.18 SQLite WAL backup/restore spike.

No imports from Mojum, Noosphere, or the canonical SQLite writer. The source
may be changed by Litestream's WAL/checkpoint machinery: this is NOT a read-only
observer. Never run against a production journal without operator review.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import signal
import sqlite3
import stat
import subprocess
import tempfile
from urllib.parse import quote

PINNED_VERSION = "0.5.18"
MAX_DB_BYTES = 128 * 1024 * 1024


class SafetyError(ValueError):
    """Fail-closed refusal of a filesystem, command or integrity precondition."""


def _absolute_path(value: str, *, kind: str, must_exist: bool = True) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise SafetyError(f"invalid {kind} path")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise SafetyError(f"invalid {kind} path")
    path = Path(value)
    if not path.is_absolute() or any(p in (".", "..") for p in path.parts):
        raise SafetyError(f"{kind} path must be absolute, without traversal")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise SafetyError(f"{kind} path contains symlink")
    if must_exist and not path.exists():
        raise SafetyError(f"{kind} path missing")
    return path


def _private_directory(directory: Path) -> None:
    if not directory.is_dir() or directory.is_symlink():
        raise SafetyError("directory missing or unsafe")
    st = directory.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_mode & 0o077:
        raise SafetyError("directory must be private (0700)")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise SafetyError("directory owner mismatch")


def _database(path: Path) -> None:
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_size < 100:
        raise SafetyError("source is not a regular SQLite database")
    if st.st_mode & 0o077:
        raise SafetyError("source database must be private (0600)")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise SafetyError("database owner mismatch")
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if os.read(fd, 16) != b"SQLite format 3\0":
            raise SafetyError("invalid SQLite database header")
    finally:
        os.close(fd)


def _file_url(path: Path) -> str:
    return "file://" + quote(str(path), safe="/")


def _validate_executable(executable: str, expected_sha256: str | None = None) -> Path:
    path = _absolute_path(executable, kind="binary")
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or not os.access(path, os.X_OK):
        raise SafetyError("binary not executable")
    if st.st_mode & 0o022:
        raise SafetyError("binary writable by group/other")
    if expected_sha256 is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise SafetyError("invalid binary SHA-256")
        if _sha256(path) != expected_sha256:
            raise SafetyError("binary SHA-256 mismatch")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _minimal_env() -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": "/nonexistent"}


def verify_version(executable: str, expected_binary_sha256: str | None = None) -> Path:
    binary = _validate_executable(executable, expected_binary_sha256)
    try:
        result = subprocess.run([str(binary), "version"], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=5,
                                env=_minimal_env(), check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SafetyError("Litestream version probe failed") from exc
    version = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or not re.search(r"(?:^|\s)v?" + re.escape(PINNED_VERSION) + r"(?:\s|$)", version):
        raise SafetyError("unsupported Litestream version (requires v" + PINNED_VERSION + ")")
    return binary


def replica_plan(source: str, replica_dir: str) -> tuple[Path, Path]:
    db = _absolute_path(source, kind="source")
    replica = _absolute_path(replica_dir, kind="replica")
    _database(db)
    _private_directory(replica)
    if db == replica or db in replica.parents or replica in db.parents:
        raise SafetyError("source and replica paths must be disjoint")
    return db, replica


def restore_plan(replica_dir: str, output: str) -> tuple[Path, Path]:
    replica = _absolute_path(replica_dir, kind="replica")
    _private_directory(replica)
    target = _absolute_path(output, kind="output", must_exist=False)
    if target.exists() or target.is_symlink():
        raise SafetyError("restore target already exists; refusing to overwrite")
    _private_directory(target.parent)
    if target in replica.parents or replica in target.parents or target == replica:
        raise SafetyError("restore target overlaps replica")
    if len(str(target)) > 4096:
        raise SafetyError("restore target path too long")
    return replica, target


def _limits(max_bytes: int):
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes, max_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    except (ImportError, OSError, ValueError) as exc:
        raise RuntimeError("OS resource limits unavailable") from exc


def _run_bounded(argv: list[str], *, timeout: int, max_bytes: int) -> None:
    if os.name != "posix":
        raise SafetyError("native runner requires POSIX resource limits")
    if not isinstance(timeout, int) or not 1 <= timeout <= 60:
        raise SafetyError("timeout out of range")
    if not isinstance(max_bytes, int) or not 1024 <= max_bytes <= MAX_DB_BYTES:
        raise SafetyError("max_bytes out of range")
    try:
        proc = subprocess.Popen(argv, env=_minimal_env(), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True, close_fds=True,
                                preexec_fn=lambda: _limits(max_bytes))
    except OSError as exc:
        raise SafetyError("Litestream start failed") from exc
    try:
        exit_code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise SafetyError("Litestream operation timed out") from exc
    if exit_code != 0:
        raise SafetyError("Litestream operation failed")


def replicate_for(executable: str, source: str, replica_dir: str, *, seconds: int,
                  expected_binary_sha256: str | None = None) -> None:
    """Timeboxed opt-in live WAL replication; intentionally not a daemon."""
    if os.name != "posix":
        raise SafetyError("replication needs POSIX process-group control")
    if not isinstance(seconds, int) or not 1 <= seconds <= 60:
        raise SafetyError("duration must be 1..60 seconds")
    db, replica = replica_plan(source, replica_dir)
    binary = verify_version(executable, expected_binary_sha256)
    try:
        proc = subprocess.Popen([str(binary), "replicate", str(db), _file_url(replica)],
                                env=_minimal_env(), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                close_fds=True, start_new_session=True,
                                preexec_fn=lambda: _limits(MAX_DB_BYTES))
    except OSError as exc:
        raise SafetyError("Litestream replication start failed") from exc
    try:
        code = proc.wait(timeout=seconds)
        if code != 0:
            raise SafetyError("Litestream replication stopped with an error")
        raise SafetyError("Litestream replication unexpectedly stopped")
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise SafetyError("Litestream did not stop cleanly") from exc
        if proc.returncode not in (0, -signal.SIGTERM):
            raise SafetyError("Litestream replication exited abnormally")


def restore_verified(executable: str, replica_dir: str, output: str, *,
                     expected_sha256: str, timeout: int = 30,
                     max_bytes: int = MAX_DB_BYTES,
                     expected_binary_sha256: str | None = None) -> str:
    """Restore into staging, check SQLite and trusted digest, publish no-clobber."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise SafetyError("trusted expected SHA-256 required for publication")
    replica, target = restore_plan(replica_dir, output)
    binary = verify_version(executable, expected_binary_sha256)
    if not isinstance(max_bytes, int) or not 1024 <= max_bytes <= MAX_DB_BYTES:
        raise SafetyError("max_bytes out of range")
    with tempfile.TemporaryDirectory(prefix=".mojum-litestream-", dir=target.parent) as tmp:
        staged = Path(tmp) / "staged.db"
        _run_bounded([str(binary), "restore", "-integrity-check", "full", "-o",
                      str(staged), _file_url(replica)], timeout=timeout,
                     max_bytes=max_bytes)
        if staged.is_symlink() or not staged.is_file() or staged.stat().st_size > max_bytes:
            raise SafetyError("restored database missing, invalid or oversized")
        if any(Path(str(staged) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise SafetyError("restore produced unverified SQLite sidecars")
        try:
            with sqlite3.connect(staged.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
                result = db.execute("PRAGMA integrity_check").fetchall()
                if result != [("ok",)]:
                    raise SafetyError("SQLite integrity check failed")
        except sqlite3.DatabaseError as exc:
            raise SafetyError("restored SQLite image invalid") from exc
        digest = _sha256(staged)
        if digest != expected_sha256:
            raise SafetyError("restored database SHA-256 did not match trusted anchor")
        os.chmod(staged, 0o600)
        try:
            os.link(staged, target, follow_symlinks=False)
        except OSError as exc:
            raise SafetyError("restore publication failed (target exists or unsafe)") from exc
        return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in local Litestream backup spike")
    sub = parser.add_subparsers(dest="action", required=True)
    plan = sub.add_parser("plan", help="validate DB and replica only; no subprocess")
    plan.add_argument("--source", required=True)
    plan.add_argument("--replica-dir", required=True)
    rep = sub.add_parser("replicate", help="explicit, timeboxed native WAL replication")
    rep.add_argument("--source", required=True)
    rep.add_argument("--replica-dir", required=True)
    rep.add_argument("--binary", required=True)
    rep.add_argument("--binary-sha256")
    rep.add_argument("--seconds", type=int, default=5)
    restore = sub.add_parser("restore", help="publish only if independently anchored")
    restore.add_argument("--replica-dir", required=True)
    restore.add_argument("--output", required=True)
    restore.add_argument("--binary", required=True)
    restore.add_argument("--binary-sha256")
    restore.add_argument("--expected-sha256", required=True)
    restore.add_argument("--timeout", type=int, default=30)
    restore.add_argument("--max-bytes", type=int, default=MAX_DB_BYTES)
    args = parser.parse_args(argv)
    try:
        if args.action == "plan":
            replica_plan(args.source, args.replica_dir)
        elif args.action == "replicate":
            replicate_for(args.binary, args.source, args.replica_dir,
                          seconds=args.seconds, expected_binary_sha256=args.binary_sha256)
        else:
            restore_verified(args.binary, args.replica_dir, args.output,
                             expected_sha256=args.expected_sha256,
                             expected_binary_sha256=args.binary_sha256,
                             timeout=args.timeout, max_bytes=args.max_bytes)
    except SafetyError as exc:
        parser.exit(2, "REFUSED: " + str(exc) + "\n")
    print("OK: local-only " + args.action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
