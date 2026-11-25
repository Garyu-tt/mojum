"""Private SQLite backup of a real, deterministically replayable synthetic Aquarium.

No full live-organism migration, multi-component atomicity, replication, or
cryptographic authentication. Files are PRIVATE and contain plaintext history.
"""

from __future__ import annotations

from contextlib import closing
import hashlib
from importlib.resources import files
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile

from .aquarium import Aquarium, AquariumError, MOTOR_MODES, load_cli_room
from .genesis import validate_seed
from .graph import load_graph
from .noosphere import STORE_SCHEMA, _open_readonly
from .noosphere import _read_first_event

MAX_BACKUP_BYTES = 128_000_000
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


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
    if any(os.path.lexists(str(dest) + suffix) for suffix in _SQLITE_SIDECARS):
        raise FileExistsError("checkpoint destination has existing SQLite sidecars")
    return dest


def _require_existing_source_journal(path, agent_id):
    """Reject incomplete source journals before Aquarium can create/repair SQLite.

    Export operates on existing history only. Aquarium is deliberately writable
    and bootstraps an empty store, so checking path existence is insufficient.
    """
    with _open_readonly(path) as db:
        db.execute("BEGIN")
        tables = {row["name"] for row in db.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type='table' AND name IN ('metadata', 'events')"
        )}
        if tables != {"metadata", "events"}:
            raise ValueError("export requires existing metadata and events tables")
        saved = dict(db.execute("SELECT k,v FROM metadata"))
        if saved != {"schema": STORE_SCHEMA, "owner": agent_id}:
            raise ValueError("export requires complete matching journal metadata")
        if db.execute("SELECT 1 FROM events LIMIT 1").fetchone() is None:
            raise ValueError("export requires an existing committed Aquarium event")


def _sha256(path, *, copy_to=None):
    """Hash a bounded file, optionally copying the bytes that were hashed."""
    hasher = hashlib.sha256()
    size = 0
    with open(path, "rb") as stream:
        while True:
            block = stream.read(min(1024 * 1024, MAX_BACKUP_BYTES - size + 1))
            if not block:
                break
            size += len(block)
            if size > MAX_BACKUP_BYTES:
                raise ValueError("SQLite backup exceeds size budget")
            hasher.update(block)
            if copy_to is not None:
                copy_to.write(block)
    return hasher.hexdigest()


def verify_cold_copy(path, seed, graph, *, expected_agent_id,
                     expected_journal_head, expected_sha256=None,
                     expected_tick=None, neural_mode="shadow",
                     room=None, start=(1.0, 2.0), dt_ns=10_000_000,
                     motor_mode="scripted", brain_mode="placeholder",
                     sensory_mode="none", sensory_scene=None, temporal_mode="none"):
    """Verify a complete cold SQLite history against an independently held head.

    The optional byte hash detects any change in the exact artifact. Reopening
    replays the complete PRIVATE log with the supplied Aquarium configuration.
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
    # Verification must not open an untrusted input through writable Aquarium:
    # an empty SQLite database would otherwise receive a bootstrap event.
    # A cold-copy artifact is self-contained, including any journal state.
    if any(os.path.lexists(str(source) + suffix) for suffix in _SQLITE_SIDECARS):
        raise ValueError("cold-copy verification requires a self-contained SQLite file")
    checksum = _sha256(source)
    if expected_sha256 is not None and checksum != expected_sha256:
        raise ValueError("cold-copy file checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="mojum-cold-verify-") as scratch:
        staged = Path(scratch) / "candidate.sqlite3"
        # Enforce the byte limit again while copying a file that may have
        # grown since the checksum preflight.
        with staged.open("xb") as output:
            staged_checksum = _sha256(source, copy_to=output)
        if staged_checksum != checksum:
            raise ValueError("cold-copy source changed during verification")
        # SQLite setup/replay may write: isolate all such effects in this
        # private temporary copy, never the caller's original artifact.
        with Aquarium(
            staged, seed, graph, agent_id=expected_agent_id,
            external_expected_head=expected_journal_head, neural_mode=neural_mode,
            room=room, start=start, dt_ns=dt_ns, motor_mode=motor_mode,
            brain_mode=brain_mode, sensory_mode=sensory_mode,
            sensory_scene=sensory_scene, temporal_mode=temporal_mode,
        ) as child:
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
                     room=None, start=(1.0, 2.0), dt_ns=10_000_000,
                     motor_mode="scripted", brain_mode="placeholder",
                     sensory_mode="none", sensory_scene=None, temporal_mode="none"):
    """Make and replay-verify a no-clobber SQLite backup of a running journal.

    A read-only SQLite connection takes a consistent online backup, including
    committed WAL pages. Only the temporary backup is opened for full Aquarium
    replay with the supplied Aquarium configuration. The source history is
    never opened writable during export.
    The backup includes commits visible when its page budget is checked.
    """
    source = _source_file(source_db)
    destination = _destination(destination_db)
    # Export is for a committed journal, never a request to initialize one.
    # Aquarium auto-bootstraps empty SQLite sources after a writable open.
    # Inspect the existing source read-only before allowing that side effect.
    if _read_first_event(source, agent_id) is None:
        raise ValueError("export requires an existing nonempty Aquarium journal")
    _require_existing_source_journal(source, agent_id)
    # The source is an archive input, never a writable Aquarium. Reopening
    # it through Aquarium would create a bootstrap in a valid-but-empty SQLite
    # store (and can update WAL or file permissions on failed exports).
    # SQLite's online backup API works with a read-only source connection and
    # includes committed WAL pages in one consistent snapshot.
    fd, temporary = tempfile.mkstemp(
        prefix=".mojum-cold-copy-", suffix=".sqlite3",
        dir=destination.parent,
    )
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.absolute().as_uri() + "?mode=ro",
                                     uri=True, timeout=5.0)) as original:
            original.execute("PRAGMA query_only=ON")
            # Hold one read snapshot for the budget check and the online backup.
            original.execute("BEGIN")
            if (original.execute("PRAGMA page_count").fetchone()[0]
                    * original.execute("PRAGMA page_size").fetchone()[0]
                    > MAX_BACKUP_BYTES):
                raise ValueError("source SQLite exceeds copy budget")
            with closing(sqlite3.connect(temporary)) as target:
                original.backup(target)
        # Pin a nonempty recorded head before the temporary copy is opened
        # through Aquarium. This prevents a plain/empty SQLite database from
        # being silently upgraded into a new, apparently valid Aquarium backup.
        with closing(sqlite3.connect(temporary)) as copied:
            try:
                row = copied.execute(
                    "SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1"
                ).fetchone()
            except sqlite3.Error as exc:
                raise ValueError("source lacks committed Aquarium history") from exc
            if row is None:
                raise ValueError("source lacks committed Aquarium history")
            copied_head = row[0]
        with Aquarium(
            temporary, seed, graph, agent_id=agent_id,
            external_expected_head=copied_head, neural_mode=neural_mode,
            room=room, start=start, dt_ns=dt_ns, motor_mode=motor_mode,
            brain_mode=brain_mode, sensory_mode=sensory_mode,
            sensory_scene=sensory_scene, temporal_mode=temporal_mode,
        ) as reopened:
            before = reopened.checkpoint()
            if before["journal_head"] != copied_head:
                raise AquariumError("copied journal differs from committed source head")
            count = reopened.store.count()
        # Publish a standalone file that SQLite can inspect without creating
        # WAL sidecars. Only the staged backup changes journal mode.
        with closing(sqlite3.connect(temporary)) as copied:
            if copied.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                raise AquariumError("cannot finalize standalone SQLite backup")
        if any(os.path.lexists(temporary + suffix) for suffix in _SQLITE_SIDECARS):
            raise AquariumError("unexported SQLite backup sidecar")
        checksum = _sha256(temporary)
        with open(temporary, "r+b") as stream:
            os.fsync(stream.fileno())
        # Publish the validated artifact under a new name, never clobbering.
        os.link(temporary, _destination(destination))
    finally:
        for suffix in ("", *_SQLITE_SIDECARS):
            try:
                os.unlink(temporary + suffix)
            except FileNotFoundError:
                pass
    return {"schema": "mojum.aquarium.cold-copy/1",
            "agent_id": agent_id, "tick": before["tick"],
            "journal_head": before["journal_head"],
            "db_sha256": checksum, "event_count": count,
            "status": "VERIFIED_SYNTHETIC_REPLAY_NOT_LIVE_MIGRATION"}


def main():
    """Offline single-host CLI. Prints only verification metadata, never events."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="PRIVATE synthetic Aquarium SQLite cold-copy/replay check",
    )
    packaged = files("mojum").joinpath("data")
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("export", "verify"):
        p = modes.add_parser(mode)
        p.add_argument("--db", type=Path, required=True)
        p.add_argument("--genome", type=Path, default=packaged.joinpath("genesis_seed.json"))
        p.add_argument("--graph", type=Path, default=packaged.joinpath("first_loop.json"))
        p.add_argument("--room-file", type=Path,
                       help="room draft used when creating the source journal")
        p.add_argument("--agent-id", help="journal owner, defaults to the supplied genome ID")
        p.add_argument("--neural-mode", choices=("shadow", "disabled"), default="shadow")
        p.add_argument("--motor-mode", choices=MOTOR_MODES, default="scripted")
        p.add_argument("--brain-mode", choices=("placeholder", "developmental_controller"),
                       default="placeholder")
        p.add_argument("--sensory-mode", choices=("none", "toy_probe", "toy_probe_v2"),
                       default="none")
        p.add_argument("--temporal-mode", choices=("none", "range_guided"), default="none")
        if mode == "export":
            p.add_argument("--output", type=Path, required=True)
        else:
            p.add_argument("--expected-head", required=True)
            p.add_argument("--expected-sha256")
            p.add_argument("--expected-tick", type=int)
    args = parser.parse_args()
    try:
        room, start = load_cli_room(args.room_file, args.motor_mode)
        options = dict(neural_mode=args.neural_mode, motor_mode=args.motor_mode,
                       brain_mode=args.brain_mode, sensory_mode=args.sensory_mode,
                       temporal_mode=args.temporal_mode, room=room, start=start)
        seed = validate_seed(json.loads(args.genome.read_text(encoding="utf-8")))
        graph = load_graph(args.graph)
        agent_id = seed["genome_id"] if args.agent_id is None else args.agent_id
        if args.mode == "export":
            result = export_cold_copy(args.db, args.output, seed, graph,
                                      agent_id=agent_id, **options)
        else:
            result = verify_cold_copy(args.db, seed, graph,
                                      expected_agent_id=agent_id,
                                      expected_journal_head=args.expected_head,
                                      expected_sha256=args.expected_sha256,
                                      expected_tick=args.expected_tick, **options)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
