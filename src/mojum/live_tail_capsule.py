"""R0.18 explicit point-in-time PRIVATE backup of an unsealed live tail.

A tail capsule contains an IMMUTABLE SNAPSHOT of 1..segment_size-1 events
following fully archived segments. It never rotates, truncates, deletes or
modifies the live SQLite source and never exports PRIVATE data by default.

A separately trusted source head is REQUIRED for both capture and restore.
A source-only same-disk backup is not a redundant device/independent replica.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile

from .noosphere import _ZERO, _canonical, _validate_event, NoosphereError
from .segmented_noosphere import (
    SegmentedNoosphere, _hash_event, MIN_SEGMENT_SIZE, MAX_SEGMENT_SIZE,
    MAX_BATCH,
)
from .segment_archive import (
    ArchiveError, SCHEMA as SEALED_SCHEMA, MAX_SEGMENT_BYTES,
    private_directory, publish_new, fsync_directory, names,
)
from .segment_archive_verify import (
    _decode, _digest, _file, read_manifest, iter_segment_events,
    verify_sealed_archive, restore_into_new_sqlite,
)

SCHEMA = "mojum.noosphere.live-tail-capsule/1"
_FIELDS = {
    "schema", "agent_id", "segment_size", "segment_index", "first_seq",
    "last_seq", "event_count", "prior_segment_head", "snapshot_head",
    "data_sha256", "data_bytes", "contains_private", "encoding",
}
MAX_TAIL_BYTES = MAX_SEGMENT_BYTES


def tail_names(first_seq, last_seq):
    prefix = f"tail-{first_seq:012d}-{last_seq:012d}"
    return prefix + ".events.jsonl", prefix + ".manifest.json"


def _tail_manifest(root, source_events):
    if type(source_events) is not int or not 1 <= source_events <= 262144:
        raise ArchiveError("source_events must be an explicit positive event count")
    # This is a direct point-in-time identifier, never 'find the newest'
    # according to an untrusted filesystem clock.
    candidates = list(root.glob(f"tail-*-{source_events:012d}.manifest.json"))
    if len(candidates) != 1:
        raise ArchiveError("missing or ambiguous explicit live-tail capsule")
    path = candidates[0]
    with _file(path, 4096) as source:
        m = _decode(source.read(4097))
    if (type(m) is not dict or set(m) != _FIELDS
            or m["schema"] != SCHEMA
            or type(m["agent_id"]) is not str
            or not 1 <= len(m["agent_id"]) <= 256
            or type(m["segment_size"]) is not int
            or not MIN_SEGMENT_SIZE <= m["segment_size"] <= MAX_SEGMENT_SIZE
            or type(m["segment_index"]) is not int
            or m["segment_index"] < 0
            or type(m["first_seq"]) is not int
            or type(m["last_seq"]) is not int
            or m["last_seq"] != source_events
            or m["first_seq"] != m["segment_index"]*m["segment_size"]+1
            or m["first_seq"] > m["last_seq"]
            or type(m["event_count"]) is not int
            or m["event_count"] != m["last_seq"]-m["first_seq"]+1
            or not 1 <= m["event_count"] < m["segment_size"]
            or not _digest(m["prior_segment_head"])
            or not _digest(m["snapshot_head"])
            or not _digest(m["data_sha256"])
            or type(m["data_bytes"]) is not int
            or not 1 <= m["data_bytes"] <= MAX_TAIL_BYTES
            or type(m["contains_private"]) is not bool
            or m["encoding"] != "canonical-jsonl-utf8"
            or path.name != tail_names(m["first_seq"], m["last_seq"])[1]):
        raise ArchiveError("invalid canonical live-tail manifest")
    return m


def _verify_tail_data(root, m):
    filename, _ = tail_names(m["first_seq"], m["last_seq"])
    size = 0
    digest = hashlib.sha256()
    previous = m["prior_segment_head"]
    private = False
    with _file(root / filename, MAX_TAIL_BYTES) as source:
        for offset in range(m["event_count"]):
            line = source.readline(131073)
            if not line or len(line) > 131072 or not line.endswith(b"\n"):
                raise ArchiveError("missing or oversized tail event")
            size += len(line)
            if size > MAX_TAIL_BYTES:
                raise ArchiveError("tail exceeds archive byte limit")
            digest.update(line)
            row = _decode(line[:-1])
            if (type(row) is not dict or set(row) !=
                    {"seq", "event", "event_hash"}
                    or type(row["seq"]) is not int
                    or row["seq"] != m["first_seq"]+offset
                    or not _digest(row["event_hash"])):
                raise ArchiveError("invalid live-tail event wrapper")
            try:
                event = _validate_event(row["event"], m["agent_id"])
            except (NoosphereError, ValueError, TypeError) as exc:
                raise ArchiveError("invalid PRIVATE live-tail event") from exc
            calc = _hash_event(
                event, seq=row["seq"],
                segment_index=m["segment_index"], previous=previous,
            )
            if row["event_hash"] != calc:
                raise ArchiveError("live-tail chain/hash mismatch")
            previous = calc
            private |= event["visibility"] != "PUBLIC"
            yield event
        if source.read(1):
            raise ArchiveError("unexpected trailing tail events")
    if (size != m["data_bytes"]
            or digest.hexdigest() != m["data_sha256"]
            or previous != m["snapshot_head"]
            or private != m["contains_private"]):
        raise ArchiveError("live-tail payload and immutable manifest disagree")


def _sealed_prefix(root, segment_size, sealed_count):
    """Verify all physical sealed files and return independently linkable head."""
    if sealed_count == 0:
        if any(root.glob("segment-*.events.jsonl")) or any(
                root.glob("segment-*.manifest.json")):
            raise ArchiveError("unexpected sealed files for genesis-only tail")
        return _ZERO, False
    last = read_manifest(root, sealed_count-1)
    if (last["segment_size"] != segment_size
            or last["segment_index"] != sealed_count-1):
        raise ArchiveError("mismatched sealed prefix segment size")
    # The declared intermediate prefix head is NOT an independent anchor.
    # The later full source-head comparison binds the whole chain.
    prefix = verify_sealed_archive(
        root, external_expected_head=last["segment_head"],
    )
    if (prefix["sealed_segments"] != sealed_count
            or prefix["segment_size"] != segment_size):
        raise ArchiveError("sealed prefix length or size mismatch")
    return prefix["sealed_head"], prefix["contains_private"]


def verify_point_in_time(directory, *, source_events, external_expected_source_head):
    """Verify sealed physical prefix + incomplete tail using trusted final head."""
    root = private_directory(directory)
    if not _digest(external_expected_source_head):
        raise ArchiveError("independently trusted source SHA256 head required")
    m = _tail_manifest(root, source_events)
    sealed_head, prefix_private = _sealed_prefix(
        root, m["segment_size"], m["segment_index"]
    )
    if m["prior_segment_head"] != sealed_head:
        raise ArchiveError("live tail is not attached to sealed prefix")
    for _ in _verify_tail_data(root, m):
        pass
    if m["snapshot_head"] != external_expected_source_head:
        raise ArchiveError("point-in-time source differs from external trusted head")
    if m["segment_index"]:
        prefix_m = read_manifest(root, 0)
        if prefix_m["agent_id"] != m["agent_id"]:
            raise ArchiveError("tail owner differs from sealed prefix")
    return {
        "schema": SCHEMA, "agent_id": m["agent_id"],
        "segment_size": m["segment_size"],
        "sealed_segments": m["segment_index"],
        "sealed_events": m["segment_index"]*m["segment_size"],
        "tail_events": m["event_count"],
        "archived_events": source_events,
        "sealed_head": sealed_head,
        "source_head": m["snapshot_head"],
        "contains_private": (m["contains_private"] or prefix_private),
        "trust": "explicit-external-source-head-required",
    }


def capture_live_tail(store, directory, *, include_nonpublic=False,
                      external_expected_source_head):
    """Capture a single exact committed head; never claim a newer live tail.

    Sealed prefix MUST already exist in this directory. For a completely
    sealed head use the older export_sealed_segments API, not this method.
    """
    if type(store) is not SegmentedNoosphere:
        raise ArchiveError("tail capture requires segmented Noosphere")
    if include_nonpublic is not True:
        raise ArchiveError("PRIVATE tail capture requires include_nonpublic=True")
    root = private_directory(directory)
    source = store.verify_chain(
        external_expected_head=external_expected_source_head
    )
    total = source["count"]
    sealed, tail_count = divmod(total, store.segment_size)
    if not tail_count:
        raise ArchiveError("no unsealed tail at this source head")
    previous, _ = _sealed_prefix(root, store.segment_size, sealed)
    if sealed:
        prior_source = store.event_at(sealed*store.segment_size)["event_hash"]
        if prior_source != previous:
            raise ArchiveError("source sealed prefix differs from archived prefix")
    start = sealed*store.segment_size+1
    data_name, meta_name = tail_names(start, total)
    data_file, manifest_file = root/data_name, root/meta_name
    if data_file.exists() or manifest_file.exists():
        if not (data_file.exists() and manifest_file.exists()):
            raise ArchiveError("orphan partial capsule: manual quarantine required")
        report = verify_point_in_time(
            root, source_events=total,
            external_expected_source_head=source["head"],
        )
        return {
            **report, "copied": False,
            "source_events_at_capture": total,
        }
    sha = hashlib.sha256()
    bytes_written = 0
    private = False
    rows = store.iter_events(
        include_nonpublic=True, after_seq=start-1, batch_size=64,
    )

    def parts():
        nonlocal bytes_written, private
        try:
            for seq in range(start, total+1):
                try:
                    row = next(rows)
                except StopIteration as exc:
                    raise ArchiveError("source tail shortened during capture") from exc
                if row["seq"] != seq:
                    raise ArchiveError("source tail changed during capture")
                data = (_canonical(row)+"\n").encode("utf-8")
                bytes_written += len(data)
                if bytes_written > MAX_TAIL_BYTES:
                    raise ArchiveError("tail snapshot exceeds byte budget")
                sha.update(data)
                private |= row["event"]["visibility"] != "PUBLIC"
                yield data
        finally:
            rows.close()
    publish_new(data_file, parts())
    if store.count() != total or store.head() != source["head"]:
        raise ArchiveError("source advanced while tail copied; orphan requires quarantine")
    m = {
        "schema": SCHEMA, "agent_id": store.agent_id,
        "segment_size": store.segment_size,
        "segment_index": sealed, "first_seq": start,
        "last_seq": total, "event_count": tail_count,
        "prior_segment_head": previous,
        "snapshot_head": source["head"],
        "data_sha256": sha.hexdigest(),
        "data_bytes": bytes_written,
        "contains_private": private,
        "encoding": "canonical-jsonl-utf8",
    }
    publish_new(manifest_file, [_canonical(m).encode("utf-8")])
    report = verify_point_in_time(
        root, source_events=total,
        external_expected_source_head=source["head"],
    )
    return {**report, "copied": True, "source_events_at_capture": total}


def restore_point_in_time(directory, destination, *, source_events,
                          external_expected_source_head):
    """Restore verified sealed prefix AND exact live tail into a NEW SQLite.

    No recovery of events newer than the selected trusted source head,
    nor automatic merging with a divergent live writer.
    """
    root = private_directory(directory)
    out = Path(destination)
    if (out.exists() or out.is_symlink() or not out.parent.is_dir()
            or out.parent.is_symlink()):
        raise ArchiveError("destination must be an absent independent SQLite path")
    # Never publish PRIVATE plaintext into a public directory.
    private_directory(out.parent)
    proven = verify_point_in_time(
        root, source_events=source_events,
        external_expected_source_head=external_expected_source_head,
    )
    with tempfile.TemporaryDirectory(prefix=".mojum-tail-recovery-", dir=out.parent) as temp:
        scratch = Path(temp)/"recovered.sqlite3"
        if proven["sealed_segments"]:
            restore_into_new_sqlite(
                root, scratch, external_expected_head=proven["sealed_head"],
            )
        m = _tail_manifest(root, source_events)
        with SegmentedNoosphere(
            scratch, proven["agent_id"], segment_size=proven["segment_size"],
        ) as recovered:
            if recovered.head() != proven["sealed_head"]:
                raise ArchiveError("recovered sealed prefix differs from expected")
            batch = []
            for event in _verify_tail_data(root, m):
                batch.append(event)
                if len(batch) >= MAX_BATCH:
                    recovered.append_many(batch, expected_head=recovered.head())
                    batch.clear()
            if batch:
                recovered.append_many(batch, expected_head=recovered.head())
            check = recovered.verify_chain(
                external_expected_head=external_expected_source_head,
            )
            if check["count"] != source_events:
                raise ArchiveError("restored exact source count mismatch")
            checkpoint = recovered._db.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
            if checkpoint is None or checkpoint[0] != 0:
                raise ArchiveError("recovery WAL checkpoint not complete")
        wal = Path(str(scratch)+"-wal")
        if wal.exists() and wal.stat().st_size:
            raise ArchiveError("unflushed restored SQLite WAL prevents publication")
        try:
            os.link(scratch, out)
        except FileExistsError as exc:
            raise ArchiveError("destination was created concurrently; no overwrite") from exc
        fsync_directory(out.parent)
    return {
        "schema": SCHEMA, "restored_events": source_events,
        "sealed_segments": proven["sealed_segments"],
        "restored_tail_events": proven["tail_events"],
        "restored_head": external_expected_source_head,
        "trust": "restored-from-explicit-pinned-point-in-time",
    }
