"""Opt-in Borg 1.4.5 encrypted archive for verified PRIVATE Aquarium SQLite copies.

The canonical journal never becomes a Borg input. First make a WAL-safe,
replay-verified standalone copy with aquarium_cold_copy. This is a local,
single-host backup experiment, not Noosphere mesh replication or failover.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

from .aquarium_cold_copy import (
    _destination, _sha256, export_cold_copy, verify_cold_copy,
)
from .genesis import validate_seed
from .graph import load_graph

SCHEMA = "mojum.backup.borg-cold/1"
BORG_PIN = "borg 1.4.5"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}\Z")
_ENCRYPTED = frozenset((
    "repokey", "keyfile", "repokey-blake2", "keyfile-blake2",
    "repokey-aes-ocb", "keyfile-aes-ocb",
))
_PAYLOAD = "private.sqlite3"


class BorgBackupError(ValueError):
    """An unsafe or unverifiable backup operation was refused."""


def _repository(path):
    """Only existing local directories, never SSH, URLs or new repositories."""
    if os.name != "posix":
        raise BorgBackupError("this owner-only Borg profile requires POSIX")
    if not isinstance(path, (str, os.PathLike)):
        raise BorgBackupError("local Borg repository path required")
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_dir():
        raise BorgBackupError("Borg repository must be an existing local directory")
    if not candidate.is_absolute():
        raise BorgBackupError("Borg repository must be an absolute local path")
    return str(candidate.resolve())


def _archive_name(name):
    if type(name) is not str or _NAME.fullmatch(name) is None or name in (".", ".."):
        raise BorgBackupError("archive name must be 1-63 safe ASCII characters")
    return name


def _run(args, *, cwd=None):
    """Run a pinned local CLI; never interpolate shell or surface stderr.

    No user-controlled stderr or stdout is included in exceptions because
    Borg can emit file paths, secrets, host names and repository locations.
    """
    try:
        cp = subprocess.run(
            ["borg", *args], cwd=cwd, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=300, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BorgBackupError("Borg executable unavailable or timed out") from exc
    if cp.returncode != 0:
        raise BorgBackupError("Borg operation failed; inspect trusted local logs")
    return cp.stdout


def _check_borg(repo):
    if _run(["--version"]).strip() != BORG_PIN:
        raise BorgBackupError("unsupported Borg version; require 1.4.5")
    try:
        info = json.loads(_run(["info", "--json", repo]))
        mode = info["encryption"]["mode"]
        if mode not in _ENCRYPTED:
            raise BorgBackupError("Borg repository must use authenticated encryption")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, BorgBackupError):
            raise
        raise BorgBackupError("cannot verify Borg repository encryption") from exc


def _extract(repo, archive, directory):
    """Restore exactly the expected relative member to isolated 0700 scratch."""
    _run(["extract", f"{repo}::{archive}", _PAYLOAD], cwd=directory)
    restored = Path(directory) / _PAYLOAD
    try:
        mode = restored.lstat().st_mode
    except OSError as exc:
        raise BorgBackupError("archive lacks expected SQLite backup member") from exc
    if not stat.S_ISREG(mode) or restored.is_symlink():
        raise BorgBackupError("Borg member is not an ordinary SQLite file")
    # This member must be directly in the scratch root, not through a link.
    if restored.resolve().parent != Path(directory).resolve():
        raise BorgBackupError("Borg member escaped private extraction directory")
    if restored.stat().st_size > 128_000_000:
        raise BorgBackupError("Borg extracted backup exceeds size budget")
    restored.chmod(0o600)
    return restored


def archive_cold_copy(source_db, repo, archive, seed, graph, *, agent_id):
    """Publish an encrypted Borg archive only after full cold replay verification.

    The returned head and digest need to be stored INDEPENDENTLY of the
    archive: without a trusted external anchor they do not authenticate it.
    """
    repo = _repository(repo)
    archive = _archive_name(archive)
    _check_borg(repo)  # Fail before making a plaintext working copy.
    with tempfile.TemporaryDirectory(prefix="mojum-borg-stage-") as staging:
        snapshot = Path(staging) / _PAYLOAD
        meta = export_cold_copy(
            source_db, snapshot, seed, graph, agent_id=agent_id,
        )
        _run(["create", "--lock-wait", "5", "--compression", "lz4",
              f"{repo}::{archive}", _PAYLOAD], cwd=staging)
        # Refuse to claim success if Borg detects a damaged archive.
        _run(["check", "--verify-data", f"{repo}::{archive}"])
        with tempfile.TemporaryDirectory(prefix="mojum-borg-verify-") as verify_dir:
            restored = _extract(repo, archive, verify_dir)
            verify_cold_copy(
                restored, seed, graph,
                expected_agent_id=agent_id,
                expected_journal_head=meta["journal_head"],
                expected_sha256=meta["db_sha256"],
                expected_tick=meta["tick"],
            )
    return {
        "schema": SCHEMA, "archive": archive, "agent_id": agent_id,
        "journal_head": meta["journal_head"], "db_sha256": meta["db_sha256"],
        "tick": meta["tick"], "event_count": meta["event_count"],
        "status": "ENCRYPTED_ARCHIVE_RESTORED_AND_SYNTHETIC_REPLAY_VERIFIED",
    }


def restore_cold_copy(repo, archive, destination, seed, graph, *,
                      agent_id, expected_head, expected_sha256, expected_tick=None):
    """Cold-restore to a *new* path, never altering the active journal.

    expected_head and expected_sha256 must come from a separately protected
    source, not merely a manifest extracted from the same Borg archive.
    """
    repo = _repository(repo)
    archive = _archive_name(archive)
    if type(expected_head) is not str or type(expected_sha256) is not str:
        raise BorgBackupError("independently anchored head and digest are mandatory")
    target = _destination(destination)
    _check_borg(repo)
    with tempfile.TemporaryDirectory(
        prefix=".mojum-borg-restore-", dir=target.parent
    ) as scratch:
        restored = _extract(repo, archive, scratch)
        result = verify_cold_copy(
            restored, seed, graph, expected_agent_id=agent_id,
            expected_journal_head=expected_head,
            expected_sha256=expected_sha256, expected_tick=expected_tick,
        )
        # Recheck after expensive extract/replay to avoid silent overwrite.
        os.link(restored, _destination(target))
        with open(target, "rb") as stream:
            os.fsync(stream.fileno())
        fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return {
        "schema": SCHEMA, "agent_id": agent_id, "archive": archive,
        "journal_head": expected_head, "db_sha256": _sha256(target),
        "tick": result["tick"], "event_count": result["event_count"],
        "status": "NEW_PATH_VERIFIED_COLD_RESTORE_NOT_LIVE_FAILOVER",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Opt-in Borg 1.4.5 encrypted PRIVATE Aquarium cold copy"
    )
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("archive", "restore"):
        p = actions.add_parser(action)
        p.add_argument("--repo", type=Path, required=True)
        p.add_argument("--archive", required=True)
        p.add_argument("--genome", type=Path, required=True)
        p.add_argument("--graph", type=Path, required=True)
        p.add_argument("--agent-id", required=True)
        if action == "archive":
            p.add_argument("--db", type=Path, required=True)
        else:
            p.add_argument("--output", type=Path, required=True)
            p.add_argument("--expected-head", required=True)
            p.add_argument("--expected-sha256", required=True)
            p.add_argument("--expected-tick", type=int)
    args = parser.parse_args(argv)
    try:
        seed = validate_seed(json.loads(args.genome.read_text(encoding="utf-8")))
        graph = load_graph(args.graph)
        if args.action == "archive":
            result = archive_cold_copy(
                args.db, args.repo, args.archive, seed, graph,
                agent_id=args.agent_id,
            )
        else:
            result = restore_cold_copy(
                args.repo, args.archive, args.output, seed, graph,
                agent_id=args.agent_id, expected_head=args.expected_head,
                expected_sha256=args.expected_sha256,
                expected_tick=args.expected_tick,
            )
    except (ValueError, OSError) as exc:
        # Catch only safe, authored errors. Do not print arbitrary subprocess
        # output, private SQLite payloads, or file content.
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
