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

from .aquarium import Aquarium, AquariumError

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
    return dest


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


def _render_private_frames(aquarium):
    """Return bounded, validated UTF-8 bytes suitable for local File API."""
    snapshot = aquarium.checkpoint()
    count = aquarium.store.count()
    # A valid bootstrapped source is mandatory. No synthetic data generation.
    if not 1 <= count <= 10_000:
        raise AquariumError("invalid number of committed journal events")
    dataset = aquarium.store.dataset(include_nonpublic=True, limit=count)
    rows = dataset["projection"]["items"]
    if len(rows) != count:
        raise AquariumError("incomplete private event history")

    wire_parts = []
    total = 0
    frames = 0
    for row in rows:
        event = row["event"]
        if event["topic"] != "aquarium.frame":
            continue
        if event["visibility"] != "PRIVATE":
            raise AquariumError("non-private committed Aquarium frame")
        frame = event["payload"]
        if (frame.get("schema") != "mojum.aquarium.frame/1"
                or type(frame.get("tick")) is not int
                or frame["tick"] != frames):
            raise AquariumError("inconsistent committed frame sequence")
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
    if not frames or frames != snapshot["tick"]:
        raise AquariumError("expected nonempty complete Aquarium frame history")

    # Viewer recognizes checkpoint schema with stored_events; do not dump
    # internal neural weights or private genotype in the summary footer.
    footer = {
        "schema": "mojum.aquarium.checkpoint/1",
        "agent_id": snapshot["agent_id"],
        "tick": snapshot["tick"],
        "journal_head": snapshot["journal_head"],
        "stored_events": count,
        "export_status": "REPLAY_VERIFIED_SYNTHETIC_PRIVATE_NOT_AUTHENTICATED",
    }
    encoded_footer = (json.dumps(footer, sort_keys=True, ensure_ascii=False,
                                 allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    total += len(encoded_footer)
    if total > MAX_TRACE_BYTES:
        raise ValueError("private trace exceeds viewer 24 MiB budget")
    wire_parts.append(encoded_footer)
    return b"".join(wire_parts), frames, snapshot, count


def export_private_trace(source_db, output_jsonl, seed, graph, *,
                         allow_private_export=False,
                         expected_journal_head=None,
                         agent_id="dante-fixture-001",
                         neural_mode="shadow",
                         room=None, start=(1.0, 2.0),
                         dt_ns=10_000_000):
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
    source = _source(source_db)
    dest = _destination(output_jsonl)
    if source.resolve() == dest.resolve():
        raise ValueError("output must not be source SQLite")
    options = {"agent_id": agent_id, "neural_mode": neural_mode,
               "room": room, "start": start, "dt_ns": dt_ns}

    # Staging is never published and is automatically removed on errors.
    with tempfile.TemporaryDirectory(prefix="mojum-trace-stage-") as scratch:
        staged = Path(scratch) / "journal.sqlite3"
        _stage_sqlite(source, staged)
        with closing(sqlite3.connect(str(staged))) as snapshot_db:
            try:
                n = snapshot_db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            except sqlite3.DatabaseError as exc:
                raise ValueError("source has no usable Noosphere event journal") from exc
            if n == 0:
                raise ValueError("source has no committed Aquarium events")
        with Aquarium(staged, seed, graph,
                      external_expected_head=expected_journal_head,
                      **options) as replayed:
            payload, frames, checkpoint, events = _render_private_frames(replayed)

    # Atomic, no-clobber publication, with restrictive private permissions.
    # Only the completed validated bytes receive their final output filename.
    fd, stage_out = tempfile.mkstemp(
        prefix=".mojum-private-trace-", suffix=".jsonl", dir=dest.parent,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(stage_out, 0o600)
        os.link(stage_out, dest)  # EEXIST rather than overwrite; same FS.
    finally:
        try:
            os.unlink(stage_out)
        except FileNotFoundError:
            pass

    return {
        "schema": SCHEMA,
        "agent_id": checkpoint["agent_id"],
        "frames": frames,
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
    parser.add_argument("--expected-head", default=None)
    parser.add_argument("--allow-private-export", action="store_true",
                        help="explicitly acknowledge PRIVATE unencrypted local history disclosure")
    args = parser.parse_args()
    seed = json.loads(args.genome.read_text(encoding="utf-8"))
    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    meta = export_private_trace(
        args.db, args.output, seed, graph,
        agent_id=args.agent_id, neural_mode=args.neural_mode,
        expected_journal_head=args.expected_head,
        allow_private_export=args.allow_private_export,
    )
    print(json.dumps(meta, sort_keys=True, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
