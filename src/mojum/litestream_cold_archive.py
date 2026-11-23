"""Opt-in Litestream 0.5.18 one-shot LOCAL archive for PUBLIC synthetic Aquarium.

This is not encrypted PRIVATE backup, a continuous daemon, or live failover.
Canonical SQLite is never given to Litestream: only a WAL-safe replayed copy is.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

from .aquarium_cold_copy import (
    MAX_BACKUP_BYTES, _destination, _sha256, export_cold_copy, verify_cold_copy,
)

SCHEMA = "mojum.litestream-local-archive/1"
VERSION = "0.5.18"
SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_ARCHIVE_BYTES = 256_000_000
MAX_ARCHIVE_FILES = 128
MAX_ARCHIVE_DIRS = 128
MAX_ARCHIVE_DEPTH = 16
MAX_TIMEOUT_SECONDS = 180
SIDECARS = ("-wal", "-shm", "-journal")


class LitestreamArchiveError(ValueError):
    """Safe error text; never includes subprocess output or PRIVATE history."""


def _opt_in(allowed):
    if allowed is not True:
        raise LitestreamArchiveError(
            "unencrypted local Litestream archives require public_synthetic=True; "
            "PRIVATE/production archives are forbidden"
        )
    if os.name != "posix":
        raise LitestreamArchiveError("this experimental local adapter requires POSIX")


def _private_parent(path):
    parent = Path(path)
    if parent.is_symlink() or not parent.is_dir():
        raise LitestreamArchiveError("existing nonsymlink parent directory required")
    mode = parent.stat().st_mode
    if not stat.S_ISDIR(mode) or (mode & 0o077) or not (mode & 0o200):
        raise LitestreamArchiveError("backup parent must be owner-only and writable (0700)")
    return parent


def _replica_path(path, *, creating):
    path = Path(path)
    if not path.name or path.name in (".", ".."):
        raise LitestreamArchiveError("local replica directory required")
    _private_parent(path.parent)
    if creating:
        if os.path.lexists(path):
            raise FileExistsError("replica directory already exists")
    else:
        if path.is_symlink() or not path.is_dir():
            raise LitestreamArchiveError("existing local replica directory required")
        if path.stat().st_mode & 0o077:
            raise LitestreamArchiveError("replica directory mustdef _archive_budget(root):
    """Bound every tree entry and depth, including otherwise free empty dirs.

    os.scandir is consumed incrementally: unlike os.walk this never builds a
    potentially unbounded list of sibling names before checking the budget.
    """
    total = count = directory_count = 0
    pending = [(Path(root), 0)]
    while pending:
        folder, depth = pending.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        if info.st_mode & 0o077:
                            raise LitestreamArchiveError("unsafe replica directory")
                        directory_count += 1
                        if (directory_count > MAX_ARCHIVE_DIRS
                                or depth + 1 > MAX_ARCHIVE_DEPTH):
                            raise LitestreamArchiveError("local replica directory budget exceeded")
                        pending.append((Path(entry.path), depth + 1))
                    elif stat.S_ISREG(info.st_mode):
                        if info.st_mode & 0o077:
                            raise LitestreamArchiveError("unsafe replica file")
                        total += info.st_size
                        count += 1
                        if total > MAX_ARCHIVE_BYTES or count > MAX_ARCHIVE_FILES:
                            raise LitestreamArchiveError("local replica resource budget exceeded")
                    else:
                        # Symlinks, FIFOs, sockets and device nodes are never
                        # traversed and cannot be interpreted as regular files.
                        raise LitestreamArchiveError("unsafe replica entry")
        except OSError:
            raise LitestreamArchiveError("cannot inspect local replica") from None
    if not count:
        raise LitestreamArchiveError("Litestream produced no local replica files")


def _checked_binary(binary, expected_sha256):
    if not isinstance(expected_sha256, str) or SHA.fullmatch(expected_sha256) is None:
        raise LitestreamArchiveError("independently pinned executable SHA-256 required")
    exe = Path(binary)
    if exe.is_symlink() or not exe.is_file():
        raise LitestreamArchiveError("regular nonsymlink Litestream executable required")
    mode = exe.stat().st_mode
    if (mode & 0o022) or not os.access(exe, os.X_OK):
        raise LitestreamArchiveError("Litestream executable must be executable and not group/world writable")
    # _sha256 already bounds file size, so a huge substitute cannot be hashed.
    if _sha256(exe) != expected_sha256:
        raise LitestreamArchiveError("Litestream executable SHA-256 mismatch")
    return str(exe.absolute())


def _run(exe, args, *, timeout):
    if type(timeout) is not int or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise LitestreamArchiveError("timeout must be 1..180 integer seconds")
    # Deliberately do not forward cloud credentials, proxy variables, user config,
    # source paths or operational output to logs. Only literal file:// URLs.
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
           "LANG": "C", "LC_ALL": "C"}
    try:
        result = subprocess.run(
            [exe, *args], check=False, timeout=timeout,
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            umask=0o077,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LitestreamArchiveError("Litestream subprocess unavailable or timed out") from None
    if result.returncode != 0:
        raise LitestreamArchiveError("Litestream subprocess failed (output redacted)")


def _validate_version(exe):
    try:
        result = subprocess.run(
            [exe, "version"], check=False, timeout=5,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C"},
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, umask=0o077,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise LitestreamArchiveError("cannot read Litestream binary version") from None
    version = result.stdout
    if (result.returncode != 0 or len(version) > 128 or
            re.fullmatch(rb"(?:litestream\s+)?v?0\.5\.18\s*", version.strip(),
                         flags=re.IGNORECASE) is None):
        raise LitestreamArchiveError("Litestream version must be exactly 0.5.18")


def _sha_file(path):
    value = _sha256(path)
    if any(os.path.lexists(str(path) + suffix) for suffix in SIDECARS):
        raise LitestreamArchiveError("restored database is not a standalone SQLite file")
    return value


def _verify(path, seed, graph, *, head, owner, tick, digest=None, options=None):
    result = verify_cold_copy(
        path, seed, graph, expected_agent_id=owner,
        expected_journal_head=head, expected_sha256=digest,
        expected_tick=tick, **(options or {}),
    )
    if result["status"] != "VERIFIED_SYNTHETIC_REPLAY_NOT_LIVE_MIGRATION":
        raise LitestreamArchiveError("Aquarium replay was not verified")
    return result


def create_local_archive(source_db, replica_dir, seed, graph, *,
                         binary, binary_sha256, public_synthetic=False,
                         agent_id="dante-fixture-001", options=None, timeout=60):
    """Create one NEW local Litestream replica and prove restore + full replay.

    A successful result's restored_sha256, head, tick and owner must be anchored
    OUTSIDE the replica; metadata stored beside a backup is not authentication.
    No PRIVATE source is supported: local file:// replicas are plaintext.
    """
    _opt_in(public_synthetic)
    replica = _replica_path(replica_dir, creating=True)
    exe = _checked_binary(binary, binary_sha256)
    _validate_version(exe)
    replica.mkdir(mode=0o700)
    try:
        with tempfile.TemporaryDirectory(prefix=".mojum-litestream-", dir=replica.parent) as temp:
            scratch = Path(temp)
            staged = scratch / "snapshot.sqlite3"
            restored = scratch / "roundtrip.sqlite3"
            snapshot = export_cold_copy(
                source_db, staged, seed, graph, agent_id=agent_id, **(options or {})
            )
            if _sha_file(staged) != snapshot["db_sha256"]:
                raise LitestreamArchiveError("staged copy checksum mismatch")
            url = replica.as_uri()
            _run(exe, ["replicate", "-once", "-force-snapshot", str(staged), url],
                 timeout=timeout)
            _archive_budget(replica)
            _run(exe, ["restore", "-integrity-check", "full",
                       "-o", str(restored), url], timeout=timeout)
            _sha_file(restored)
            verified = _verify(restored, seed, graph, head=snapshot["journal_head"],
                               owner=snapshot["agent_id"], tick=snapshot["tick"],
                               options=options)
            return {
                "schema": SCHEMA, "litestream_version": VERSION,
                "agent_id": verified["agent_id"], "tick": verified["tick"],
                "event_count": verified["event_count"],
                "journal_head": verified["journal_head"],
                "restored_sha256": _sha_file(restored),
                "status": "LOCAL_PUBLIC_SYNTHETIC_RESTORE_VERIFIED",
                "encrypted": False, "continuous_replication": False,
            }
    except BaseException:
        # This directory was created by this call from a previously nonexistent
        # path; don't publish an unverified or partially generated archive.
        shutil.rmtree(replica)
        raise


def restore_local_archive(replica_dir, destination, seed, graph, *,
                          binary, binary_sha256, expected_agent_id,
                          expected_journal_head, expected_sha256, expected_tick,
                          public_synthetic=False, options=None, timeout=60):
    """Restore to a NEW SQLite path only after external-anchor replay proof."""
    _opt_in(public_synthetic)
    replica = _replica_path(replica_dir, creating=False)
    dest = _destination(destination)
    _private_parent(dest.parent)
    if not isinstance(expected_sha256, str) or SHA.fullmatch(expected_sha256) is None:
        raise LitestreamArchiveError("independent restored SHA-256 is mandatory")
    if not isinstance(expected_journal_head, str) or SHA.fullmatch(expected_journal_head) is None:
        raise LitestreamArchiveError("independent journal head is mandatory")
    _archive_budget(replica)
    exe = _checked_binary(binary, binary_sha256)
    _validate_version(exe)
    with tempfile.TemporaryDirectory(prefix=".mojum-litestream-restore-", dir=dest.parent) as temp:
        staged = Path(temp) / "restored.sqlite3"
        _run(exe, ["restore", "-integrity-check", "full",
                   "-o", str(staged), replica.as_uri()], timeout=timeout)
        if _sha_file(staged) != expected_sha256:
            raise LitestreamArchiveError("restored database SHA-256 mismatch")
        checked = _verify(staged, seed, graph, head=expected_journal_head,
                          owner=expected_agent_id, tick=expected_tick,
                          digest=expected_sha256, options=options)
        os.chmod(staged, 0o600)
        with open(staged, "rb") as stream:
            os.fsync(stream.fileno())
        # Atomic no-clobber publication on the same filesystem.
        os.link(staged, _destination(dest))
    return {
        "schema": SCHEMA, "agent_id": checked["agent_id"],
        "journal_head": checked["journal_head"], "tick": checked["tick"],
        "event_count": checked["event_count"], "restored_sha256": expected_sha256,
        "status": "RESTORED_NEW_PATH_VERIFIED_SYNTHETIC_ONLY",
    }
