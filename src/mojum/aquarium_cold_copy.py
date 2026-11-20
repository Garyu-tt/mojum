"""Private SQLite backup of a real, deterministically replayable synthetic Aquarium.

No full live-organism migration, multi-component atomicity, replication, or
cryptographic authentication. Files are PRIVATE and contain plaintext history.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile

from .aquarium import Aquarium, AquariumError

MAX_BACKUP_BYTES = 128_000_000
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _source_file(path):
    try:
        candidate = Path(path)
    except (TypeError, ValueError) as exc:
        raise ValueError("a local source path is required") from exc
    if str(path) == ":memory:" or not candidate.name or candidate.is_symlink():
        raise ValueError("SQLite source must be a regular local file, not a symlink")
    try:
        mode = candidate.stat().st_mode
    except OSError as exc:
        raise ValueError("SQLite source is missing or unreadable") from exc
    if not stat.S_ISREG(mode):
        raise ValueError("SQLite source must be a regular file")
    return candidate


def _destination(path):
    try:
        dest = Path(path)
    except (TypeError, ValueError) as exc:
        raise ValueError("checkpoint destination path required") from exc
    if not dest.name or not dest.parent.is_dir() or dest.parent.is_symlink():
        raise ValueError("destination requires an existing trusted local parent")
    if os.path.lexists(dest):
        raise FileExistsError("checkpoint destination already exists")
    return dest


def _sha256(path):
    hasher = hashlib.sha256()
    size = 0
    with open(path, "rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            if size > MAX_BACKUP_BYTES:
                raise ValueError("SQLite backup exceeds size budget")
            hasher.update(block)
    return hasher.hexdigest()


def _options(agent_id, neural_mode, room, start, dt_ns):
    return dict(agent_id=agent_id, neural_mode=neural_mode,
                room=room, start=start, dt_ns=dt_ns)


def verify_cold_copy(path, seed, graph, *, expected_agent_id,
                     expected_journal_head, expected_sha256=None,
                     expected_tick=None, neural_mode="shadow",
                     room=None, start=(1.0, 2.0), dt_ns=10_000_000):
    """Verify a complete cold SQLite history against an independently held head.

    The optional byte hash detects any change in the exact artifact. Reopening
    replays the complete PRIVATE log and validates all Aquarium events.
    No physical actuators or motor gates are restored.
    """
    source = _source_file(path)
    if type(expected_journal_head) is not str or _SHA.fullmatch(expected_journal_head) is None:
        raise ValueError("independently expected 64-digit journal head required")
    if expected_sha256 is not None and (
            type(expected_sha256) is not str or _SHA.fullmatch(expected_sha256) is None):
        raise ValueError("expected file SHA-256 must be lowercase 64-digit hex")
    if expected_tick is not None and (type(expected_tick) is not int or expected_tick < 0):
        raise ValueError("expected tick must be a nonnegative integer")
    if type(expected_agent_id) is not str or not expected_agent_id.strip():
        raise ValueError("expected organism ID required")
    checksum = _sha256(source)
    if expected_sha256 is not None and checksum != expected_sha256:
        raise ValueError("cold-copy file checksum mismatch")
    with Aquarium(source, seed, graph, external_expected_head=expected_journal_head,
                  **_options(expected_agent_id, neural_mode, room, start, dt_ns)) as child:
        snapshot = child.checkpoint()
        if snapshot["journal_head"] != expected_journal_head:
            raise AquariumError("unexpected journal head after replay")
        if expected_tick is not None and snapshot["tick"] != expected_tick:
            raise AquariumError("unexpected cold-copy virtual tick")
        count = child.store.count()
    return {"schema": "mojum.aquarium.cold-copy/1",
            "agent_id": expected_agent_id, "tick": snapshot["tick"],
            "journal_head": expected_journal_head,
            "db_sha256_before_replay": checksum, "event_count": count,
            "status": "VERIFIED_SYNTHETIC_REPLAY_NOT_LIVE_MIGRATION"}


def export_cold_copy(source_db, destination_db, seed, graph, *,
                     agent_id="dante-fixture-001", neural_mode="shadow",
                     room=None, start=(1.0, 2.0), dt_ns=10_000_000):
    """Make and replay-verify a no-clobber SQLite backup of a running journal.

    The Aquarium source is opened and fully replayed first; SQLite's backup
    API includes committed WAL pages, unlike a plain copy of an open .sqlite3.
    If a concurrent writer changes the source between replay and backup,
    the copied head/checkpoint must still match or publication is refused.
    """
    source = _source_file(source_db)
    destination = _destination(destination_db)
    opts = _options(agent_id, neural_mode, room, start, dt_ns)
    with Aquarium(source, seed, graph, **opts) as original:
        before = original.checkpoint()
        count = original.store.count()
        connection = original.store._db  # LocalNoosphere's live SQLite handle.
        if (connection.execute("PRAGMA page_count").fetchone()[0]
                * connection.execute("PRAGMA page_size").fetchone()[0]
                > MAX_BACKUP_BYTES):
            raise ValueError("source SQLite exceeds copy budget")
        fd, temporary = tempfile.mkstemp(
            prefix=".mojum-cold-copy-", suffix=".sqlite3",
            dir=destination.parent,
        )
        os.close(fd)
        try:
            with sqlite3.connect(temporary) as target:
                connection.backup(target)
            with Aquarium(temporary, seed, graph,
                          external_expected_head=before["journal_head"],
                          **opts) as reopened:
                if reopened.checkpoint() != before:
                    raise AquariumError("copied journal differs from validated source")
            # Reopening in WAL mode must not leave any unexported sidecars.
            if any(os.path.exists(temporary + suffix) for suffix in ("-wal", "-shm")):
                raise AquariumError("unexported SQLite backup WAL sidecar")
            checksum = _sha256(temporary)
            with open(temporary, "rb") as stream:
                os.fsync(stream.fileno())
            # Same-filesystem hard link atomically publishes a *new* name.
            # Unlike os.replace, it cannot overwrite a preexisting destination.
            os.link(temporary, destination)
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(temporary + suffix)
                except FileNotFoundError:
                    pass
    return {"schema": "mojum.aquarium.cold-copy/1",
            "agent_id": agent_id, "tick": before["tick"],
            "journal_head": before["journal_head"],
            "db_sha256": checksum, "event_count": count,
            "status": "VERIFIED_SYNTHETIC_REPLAY_NOT_LIVE_MIGRATION"}
