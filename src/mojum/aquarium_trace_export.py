"""Read-only, replay-verified PRIVATE Aquarium SQLite -> offline viewer JSONL.

This is a CPU/stdlib single-device *synthetic* event-history exporter, not a
live sensor stream, encrypted backup, authenticated history or brain migration.
The original database connection is opened read-only; all replay is against
a private, disposable SQLite Backup API snapshot.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile

from .aquarium import Aquarium, AquariumError, MAX_REPLAY_EVENTS

SCHEMA = "mojum.aquarium.trace-export/1"
MAX_SOURCE_BYTES = 128_000_000
MAX_TRACE_BYTES = 24 * 1024 * 1024
MAX_FRAMES = 5000
MAX_JSONL_CHARS = 64_000
_HEAD = re.compile(r"[0-9a-f]{64}\Z")


def _source(path):
    candidate = Path(path)
    if str(path) == ":memory:" or not candidate.name or candidate.is_symlink():
        raise ValueError("source SQLite must be a regular existing file, not a symlink")
    try:
        info = candidate.stat()
    except OSError as exc:
        raise ValueError("source SQLite file is missing or unreadable") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("source SQLite must be a regular file")
    if info.st_size > MAX_SOURCE_BYTES:
        raise ValueError("source SQLite exceeds 128 MB budget")
    return candidate


def _destination(path):
    dest = Path(path)
    if (not dest.name or not dest.parent.is_dir()
            or dest.parent.is_symlink()
            or os.path.lexists(dest)):
        raise ValueError("output must be a new filename in an existing trusted directory")
    if os.name == "posix":
        # PRIVATE JSONL is 0600, but a shared parent leaks filenames and
        # enables other accounts to race source/target directory entries.
        # Match the independent read-only orphan inspector's owner-only
        # directory requirement. Never silently chmod an operator's folder.
        parent = dest.parent
        info = parent.stat(follow_symlinks=False)
        if (not stat.S_ISDIR(info.st_mode) or
                info.st_uid != os.geteuid() or
                stat.S_IMODE(info.st_mode) & 0o077 or
                (stat.S_IMODE(info.st_mode) & 0o300) != 0o300):
            raise PermissionError(
                "PRIVATE trace output directory must be owner-owned, "
                "owner-writable/searchable, and not accessible to group/other"
            )
        # Check the entire provided parent path, not just the last
        # component. This is a fail-closed PREFLIGHT check, not a promise
        # that an adversary cannot rename it after validation.
        if parent.resolve(strict=True) != parent.absolute():
            raise PermissionError(
                "PRIVATE trace output directory has a symlink or alias ancestor"
            )
    return dest


def _fsync_published_directory(directory):
    """Durably request POSIX namespace publication after file fsync+link.

    Called only AFTER the new link is published and the private stage path
    has been unlinked. A failure leaves a complete but durability-uncertain
    final file in place: callers must reconcile, never silently retry-overwrite.

    No claim of actual electrical power-cut safety, controller caches, flash
    translation guarantees, remote filesystems or Windows directory sync.
    """
    if os.name != "posix":
        # No portable directory fsync equivalent is implemented here.
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(directory), flags)
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("expected a trusted output directory for fsync")
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def _limit_source_pages(source):
    pages = source.execute("PRAGMA page_count").fetchone()[0]
    bytes_per_page = source.execute("PRAGMA page_size").fetchone()[0]
    if pages * bytes_per_page > MAX_SOURCE_BYTES:
        raise ValueError("SQLite source snapshot exceeds 128 MB budget")


def _stage_sqlite(source, stage):
    # As a read-only SQLite connection, this never writes source database
    # pages, bootstrap events, metadata, or application PRAGMAs.
    # SQLite may still use preexisting -wal/-shm synchronization sidecars.
    uri = source.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as original:
        original.execute("PRAGMA trusted_schema=OFF")
        _limit_source_pages(original)
        with closing(sqlite3.connect(str(stage))) as target:
            original.backup(target, pages=256, sleep=0.05)
    if stage.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("copied SQLite snapshot exceeds 128 MB budget")


def _render_private_frames(aquarium, *, start_tick=None, frame_limit=None):
    """Verify full history but emit only an explicitly requested bounded slice.

    With neither range argument the behavior stays full-history or fail:
    never silently truncate the user's private event log.
    """
    snapshot = aquarium.checkpoint()
    count = aquarium.store.count()
    # A valid bootstrapped source is mandatory. No synthetic data generation.
    if not 1 <= count <= aquarium.journal_limit:
        raise AquariumError("invalid number of committed journal events")

    wire_parts = []
    total = 0
    total_frames = 0
    frames = 0
    epoch_boundaries = []
    events_seen = 0
    last_event_hash = None
    range_begin = 0 if start_tick is None else start_tick
    range_end = None if start_tick is None else start_tick + frame_limit
    for row in aquarium.store.iter_events(include_nonpublic=True):
        events_seen += 1
        if row["seq"] != events_seen:
            raise AquariumError("incomplete committed journal stream")
        last_event_hash = row["event_hash"]
        event = row["event"]
        if event["topic"] == "aquarium.epoch":
            if aquarium.developmental_policy != "local_cell_v4":
                raise AquariumError("epoch event in non-v4 journal")
            marker = event["payload"]
            tick = marker.get("absolute_tick")
            if (type(tick) is not int or tick <= 0
                    or marker.get("schema") != "mojum.aquarium.epoch-transition/1"
                    or marker.get("epoch_index") != tick // 9999
                    or tick % 9999 != 0):
                raise AquariumError("invalid replayed epoch marker")
            if tick >= range_begin and (range_end is None or tick < range_end):
                epoch_boundaries.append({
                    "schema": marker["schema"],
                    "absolute_tick": tick,
                    "epoch_index": marker["epoch_index"],
                })
            continue
        if event["topic"] != "aquarium.frame":
            continue
        if event["visibility"] != "PRIVATE":
            raise AquariumError("non-private committed Aquarium frame")
        frame = event["payload"]
        if (frame.get("schema") != "mojum.aquarium.frame/1"
                or type(frame.get("tick")) is not int
                or frame["tick"] != total_frames):
            raise AquariumError("inconsistent committed frame sequence")
        total_frames += 1
        if frame["tick"] < range_begin or (range_end is not None and frame["tick"] >= range_end):
            continue
        frames += 1
        if frames > MAX_FRAMES:
            raise ValueError("viewer supports at most 5000 frames; refusing truncation")
        line = json.dumps(frame, sort_keys=True, ensure_ascii=False,
                          allow_nan=False, separators=(",", ":"))
        if len(line) > MAX_JSONL_CHARS:
            raise ValueError("committed private frame exceeds viewer line budget")
        encoded = (line + "\n").encode("utf-8")
        total += len(encoded)
        if total > MAX_TRACE_BYTES:
            raise ValueError("private trace exceeds viewer 24 MiB budget")
        wire_parts.append(encoded)
    if events_seen != count or last_event_hash != snapshot["journal_head"]:
        raise AquariumError("incomplete or advanced private event history")
    if not total_frames or total_frames != snapshot["tick"]:
        raise AquariumError("expected nonempty complete Aquarium frame history")
    if not frames:
        raise ValueError("requested trace segment has no committed frames")
    next_tick = range_begin + frames
    has_more = next_tick < total_frames

    # Digest the exact UTF-8 frame lines, including each trailing newline.
    # This detects accidental JSONL corruption/truncation after safe SQLite
    # replay. It is NOT a signature and does not authenticate a maliciously
    # replaced file without an independently trusted digest.
    frames_wire = b"".join(wire_parts)
    trace_integrity = {
        "schema": "mojum.aquarium.trace-frames-sha256/1",
        "sha256": hashlib.sha256(frames_wire).hexdigest(),
        "byte_length": len(frames_wire),
        "frame_count": frames,
    }

    # Viewer recognizes checkpoint schema with stored_events; do not dump
    # internal neural weights or private genotype in the summary footer.
    footer = {
        "schema": snapshot["schema"],
        "agent_id": snapshot["agent_id"],
        "tick": snapshot["tick"],
        "journal_head": snapshot["journal_head"],
        "stored_events": count,
        "trace_integrity": trace_integrity,
        "trace_range": {"start_tick": range_begin, "next_tick": next_tick,
                        "has_more": has_more, "source_total_ticks": total_frames},
        "export_status": "REPLAY_VERIFIED_SYNTHETIC_PRIVATE_NOT_AUTHENTICATED",
    }
    if aquarium.developmental_policy == "local_cell_v4":
        # Only public-format version identity and actual boundary receipts.
        # Never place the source's developmental/sensory model state here.
        if snapshot["schema"] != "mojum.aquarium.checkpoint/2":
            raise AquariumError("v4 checkpoint must be version /2")
        if snapshot["tick"] != total_frames:
            raise AquariumError("v4 frame count differs from canonical clock")
        footer.update({
            "epoch_index": snapshot["epoch_index"],
            "epoch_tick": snapshot["epoch_tick"],
            "policy_schema": snapshot["developmental_state"]["schema"],
            "epoch_boundaries": epoch_boundaries,
        })
        if footer["policy_schema"] != "mojum.developmental.local-cell/4":
            raise AquariumError("v4 checkpoint policy wire mismatch")
        expected_markers = [
            tick for tick in range(range_begin, next_tick)
            if tick > 0 and tick % 9999 == 0
        ]
        if [m["absolute_tick"] for m in epoch_boundaries] != expected_markers:
            raise AquariumError("missing or duplicate committed epoch marker")
    encoded_footer = (json.dumps(footer, sort_keys=True, ensure_ascii=False,
                                 allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    total += len(encoded_footer)
    if total > MAX_TRACE_BYTES:
        raise ValueError("private trace exceeds viewer 24 MiB budget")
    wire_parts.append(encoded_footer)
    return b"".join(wire_parts), frames, snapshot, count, range_begin, next_tick, has_more


def export_private_trace(source_db, output_jsonl, seed, graph, *,
                         allow_private_export=False,
                         expected_journal_head=None,
                         agent_id="dante-fixture-001",
                         neural_mode="shadow",
                         room=None, start=(1.0, 2.0),
                         exploration_mode="scripted", sensory_mode="none",
                         sensory_scene=None, dt_ns=10_000_000,
                         start_tick=None, frame_limit=None,
                         developmental_policy="legacy",
                         journal_backend="legacy", journal_segment_size=4096):
    """Export real verified SQLite events only to a fresh, mode-0600 local file.

    All integrity checks happen on a consistent temporary SQLite snapshot,
    never on the original with a writable LocalNoosphere connection.
    The independently supplied expected head, when present, is required to
    match the captured snapshot. No cryptographic signing is offered.
    """
    if allow_private_export is not True:
        raise PermissionError("explicit PRIVATE history export opt-in required")
    if expected_journal_head is not None and (
            type(expected_journal_head) is not str
            or _HEAD.fullmatch(expected_journal_head) is None):
        raise ValueError("independent journal head must be lowercase 64-digit SHA-256")
    if type(developmental_policy) is not str or developmental_policy not in (
            "legacy", "local_cell_v2", "local_cell_v3", "local_cell_v4"):
        raise ValueError("unknown explicit developmental policy")
    if type(journal_backend) is not str or journal_backend not in ("legacy", "segmented"):
        raise ValueError("unknown explicit source journal backend")
    if type(journal_segment_size) is not int or not 64 <= journal_segment_size <= 8192:
        raise ValueError("segment size must be an integer in 64..8192")
    if journal_backend == "segmented" and developmental_policy != "local_cell_v4":
        raise ValueError("segmented trace requires explicit local_cell_v4 mode")
    if developmental_policy == "local_cell_v4" and expected_journal_head is None:
        raise ValueError("v4 private export requires an independent journal head")
    if (start_tick is None) != (frame_limit is None):
        raise ValueError("start_tick and frame_limit must be supplied together")
    if start_tick is not None:
        if type(start_tick) is not int or not 0 <= start_tick < 99999:
            raise ValueError("start_tick must be an integer in 0..99998")
        if type(frame_limit) is not int or not 1 <= frame_limit <= MAX_FRAMES:
            raise ValueError("frame_limit must be an integer in 1..5000")
    source = _source(source_db)
    dest = _destination(output_jsonl)
    if source.resolve() == dest.resolve():
        raise ValueError("output must not be source SQLite")
    # Pass the *exact* declared Aquarium modes into deterministic cold replay.
    # Never downgrade a developmental journal to scripted playback or derive
    # hidden toy truth from private records to bypass bootstrap validation.
    options = {"agent_id": agent_id, "neural_mode": neural_mode,
               "exploration_mode": exploration_mode, "sensory_mode": sensory_mode,
               "sensory_scene": sensory_scene, "room": room,
               "start": start, "dt_ns": dt_ns,
               "developmental_policy": developmental_policy,
               "journal_backend": journal_backend,
               "journal_segment_size": journal_segment_size}

    # Staging is never published and is automatically removed on errors.
    with tempfile.TemporaryDirectory(prefix="mojum-trace-stage-") as scratch:
        staged = Path(scratch) / "journal.sqlite3"
        _stage_sqlite(source, staged)
        with closing(sqlite3.connect(str(staged))) as snapshot_db:
            try:
                table = "segmented_events" if journal_backend == "segmented" else "events"
                n = snapshot_db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            except sqlite3.DatabaseError as exc:
                raise ValueError("source has no usable Noosphere event journal") from exc
            if n == 0:
                raise ValueError("source has no committed Aquarium events")
        with Aquarium(staged, seed, graph,
                      external_expected_head=expected_journal_head,
                      **options) as replayed:
            payload, frames, checkpoint, events, first_tick, next_tick, has_more = (
                _render_private_frames(replayed, start_tick=start_tick,
                                       frame_limit=frame_limit)
            )

    # Atomic, no-clobber publication, with restrictive private permissions.
    # Only the completed validated bytes receive their final output filename.
    fd, stage_out = tempfile.mkstemp(
        prefix=".mojum-private-trace-", suffix=".jsonl", dir=dest.parent,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            # The temp inode is 0600 from mkstemp. Reinforce that promise
            # on its OWN open descriptor *before* flushing inode metadata;
            # changing mode after file fsync would leave new permissions
            # outside that explicit durability boundary.
            if os.name == "posix":
                os.fchmod(stream.fileno(), 0o600)
            else:
                os.chmod(stage_out, 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(stage_out, dest)  # EEXIST rather than overwrite; same FS.
    finally:
        try:
            os.unlink(stage_out)
        except FileNotFoundError:
            pass

    # File fsync alone is not enough to request persistence of the directory
    # entry. Sync AFTER the stage unlink so successful POSIX publication
    # includes both the new hard-link and removal of its PRIVATE temp name.
    # If this fails, the final file MAY ALREADY EXIST. Propagate failure;
    # do not claim success, delete/overwrite the output, or hide the error.
    _fsync_published_directory(dest.parent)

    return {
        "schema": SCHEMA,
        "agent_id": checkpoint["agent_id"],
        "frames": frames,
        "first_tick": first_tick,
        "next_tick": next_tick,
        "has_more": has_more,
        "source_total_ticks": checkpoint["tick"],
        "event_count": events,
        "journal_head": checkpoint["journal_head"],
        "trace_sha256": hashlib.sha256(payload).hexdigest(),
        "classification": "PRIVATE_SYNTHETIC_REPLAY_NOT_AUTHENTICATED",
        "output_permissions": "0600_on_POSIX",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Export replay-verified PRIVATE synthetic Aquarium events to offline JSONL",
    )
    root = Path(__file__).resolve().parents[2]
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--genome", type=Path, default=root / "examples/genesis_seed.json")
    parser.add_argument("--graph", type=Path, default=root / "examples/first_loop.json")
    parser.add_argument("--agent-id", default="dante-fixture-001")
    parser.add_argument("--neural-mode", choices=("shadow", "disabled"), default="shadow")
    parser.add_argument("--exploration-mode", choices=("scripted", "goal_free"),
                        default="scripted")
    parser.add_argument("--sensory-mode", choices=("none", "toy_probe", "toy_probe_v2"),
                        default="none")
    parser.add_argument("--developmental-policy",
                        choices=("legacy", "local_cell_v2", "local_cell_v3", "local_cell_v4"),
                        default="legacy")
    parser.add_argument("--journal-backend", choices=("legacy", "segmented"),
                        default="legacy")
    parser.add_argument("--journal-segment-size", type=int, default=4096)
    parser.add_argument("--expected-head", default=None)
    parser.add_argument("--start-tick", type=int)
    parser.add_argument("--frame-limit", type=int)
    parser.add_argument("--allow-private-export", action="store_true",
                        help="explicitly acknowledge PRIVATE unencrypted local history disclosure")
    args = parser.parse_args()
    seed = json.loads(args.genome.read_text(encoding="utf-8"))
    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    meta = export_private_trace(
        args.db, args.output, seed, graph,
        agent_id=args.agent_id, neural_mode=args.neural_mode,
        exploration_mode=args.exploration_mode, sensory_mode=args.sensory_mode,
        expected_journal_head=args.expected_head,
        allow_private_export=args.allow_private_export,
        start_tick=args.start_tick, frame_limit=args.frame_limit,
        developmental_policy=args.developmental_policy,
        journal_backend=args.journal_backend,
        journal_segment_size=args.journal_segment_size,
    )
    print(json.dumps(meta, sort_keys=True, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
