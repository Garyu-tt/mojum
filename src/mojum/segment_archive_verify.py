"""R0.17 strict read-only physical archive verification and sealed restore.

A full segment consists of independently stored PRIVATE .jsonl data and
0600 immutable manifest, each linked to the preceding segment's head.
Archive SHA256 is not an authenticated identity without a trusted
external expected sealed-prefix head (supplied by the caller).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from .noosphere import EMPTY_HEAD, _canonical, _validate_event, NoosphereError
from .segmented_noosphere import (
    SegmentedNoosphere, _hash_event, MAX_SEGMENT_SIZE, MIN_SEGMENT_SIZE,
    MAX_BATCH, MAX_JOURNAL_EVENTS,
)
from .segment_archive import (
    ArchiveError, SCHEMA, MAX_EVENT_LINE_BYTES, private_directory,
    names, fsync_directory,
)

_FIELDS = {
    "schema", "agent_id", "segment_index", "segment_size",
    "first_seq", "last_seq", "event_count", "prior_segment_head",
    "segment_head", "data_sha256", "data_bytes", "contains_private",
    "encoding",
}
_HEX = frozenset("0123456789abcdef")
_DATA_RE = re.compile(r"segment-[0-9]{8}\.events\.jsonl\Z", re.ASCII)
_META_RE = re.compile(r"segment-[0-9]{8}\.manifest\.json\Z", re.ASCII)
# append_many holds both the input and the checked event copies.
_RESTORE_BATCH_BYTES = 1024 * 1024


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in _HEX for c in value)


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ArchiveError("duplicate JSON key in physical archive")
        out[key] = value
    return out


def _decode(raw):
    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        if _canonical(parsed).encode("utf-8") != raw:
            raise ArchiveError("noncanonical archive JSON")
        return parsed
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ArchiveError("invalid canonical archive JSON") from exc


def _file(path, size_limit):
    if path.is_symlink():
        raise ArchiveError("symlink in sealed archive")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise ArchiveError("missing/unreadable sealed archive member") from exc
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_size > size_limit:
            raise ArchiveError("archive member must be bounded regular file")
        if os.name == "posix" and stat.S_IMODE(s.st_mode) & 0o077:
            raise ArchiveError("archive member must be owner-only 0600")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def read_manifest(root, index):
    _, meta = names(index)
    with _file(root / meta, 4096) as handle:
        raw = handle.read(4097)
    m = _decode(raw)
    if (type(m) is not dict or set(m) != _FIELDS
            or m["schema"] != SCHEMA
            or type(m["agent_id"]) is not str
            or not 1 <= len(m["agent_id"]) <= 256
            or type(m["segment_index"]) is not int
            or m["segment_index"] != index
            or type(m["segment_size"]) is not int
            or not MIN_SEGMENT_SIZE <= m["segment_size"] <= MAX_SEGMENT_SIZE
            or type(m["first_seq"]) is not int
            or m["first_seq"] != index*m["segment_size"]+1
            or type(m["last_seq"]) is not int
            or m["last_seq"] != (index+1)*m["segment_size"]
            or m["last_seq"] > MAX_JOURNAL_EVENTS
            or type(m["event_count"]) is not int
            or m["event_count"] != m["segment_size"]
            or not _digest(m["prior_segment_head"])
            or not _digest(m["segment_head"])
            or not _digest(m["data_sha256"])
            or type(m["data_bytes"]) is not int
            or not 1 <= m["data_bytes"] <= m["event_count"] * MAX_EVENT_LINE_BYTES
            or type(m["contains_private"]) is not bool
            or m["encoding"] != "canonical-jsonl-utf8"):
        raise ArchiveError("invalid physical sealed segment manifest")
    return m


def iter_segment_events(root, manifest, previous, *, include_line_size=False):
    """Yield validated events, finish with full physical SHA/head verification.

    With include_line_size, yield (event, encoded line bytes) pairs.
    Consumer MUST exhaust this iterator or it has NOT verified the member.
    """
    data_name, _ = names(manifest["segment_index"])
    if manifest["prior_segment_head"] != previous:
        raise ArchiveError("archive segment does not link to prior head")
    digest = hashlib.sha256()
    size = 0
    count = 0
    nonpublic = False
    head = previous
    with _file(root / data_name, manifest["data_bytes"]) as source:
        while count < manifest["event_count"]:
            line = source.readline(MAX_EVENT_LINE_BYTES + 1)
            line_size = len(line)
            if (not line or line_size > MAX_EVENT_LINE_BYTES
                    or not line.endswith(b"\n")):
                raise ArchiveError("missing/oversized physical event line")
            size += line_size
            if size > manifest["data_bytes"]:
                raise ArchiveError("physical segment exceeds declared data size")
            digest.update(line)
            row = _decode(line[:-1])
            if (type(row) is not dict or set(row) !=
                    {"seq", "event", "event_hash"}
                    or type(row["seq"]) is not int
                    or row["seq"] != manifest["first_seq"]+count
                    or not _digest(row["event_hash"])):
                raise ArchiveError("invalid sequence or event hash wrapper")
            try:
                event = _validate_event(row["event"], manifest["agent_id"])
            except (ValueError, TypeError, NoosphereError) as exc:
                raise ArchiveError("untrusted Noosphere event in archive") from exc
            computed = _hash_event(
                event, seq=row["seq"],
                segment_index=manifest["segment_index"], previous=head,
            )
            if computed != row["event_hash"]:
                raise ArchiveError("sealed event hash chain mismatch")
            nonpublic |= event["visibility"] != "PUBLIC"
            head = computed
            count += 1
            yield (event, line_size) if include_line_size else event
        if source.read(1):
            raise ArchiveError("unexpected trailing archive records")
    if (size != manifest["data_bytes"]
            or digest.hexdigest() != manifest["data_sha256"]
            or head != manifest["segment_head"]
            or nonpublic != manifest["contains_private"]):
        raise ArchiveError("archive data does not match immutable manifest")


def verify_segment(directory, index, *, prior, expected_agent, expected_size):
    root = private_directory(directory)
    m = read_manifest(root, index)
    if (m["agent_id"] != expected_agent
            or m["segment_size"] != expected_size):
        raise ArchiveError("archive member is from different agent/size")
    for _ in iter_segment_events(root, m, prior):
        pass
    return m


def _indices(root, *, allow_empty=False):
    manifests = set()
    data = set()
    max_segments = MAX_JOURNAL_EVENTS // MIN_SEGMENT_SIZE
    with os.scandir(root) as entries:
        for entry in entries:
            name = entry.name
            if not name.startswith("segment-"):
                continue
            if name.endswith(".manifest.json"):
                if _META_RE.fullmatch(name) is None:
                    raise ArchiveError("unexpected archive manifest name")
                manifests.add(name)
            elif name.endswith(".events.jsonl"):
                if _DATA_RE.fullmatch(name) is None:
                    raise ArchiveError("unexpected archive data filename")
                data.add(name)
            if max(len(data), len(manifests)) > max_segments:
                raise ArchiveError("archive member count exceeds bounded policy")
    if len(data) != len(manifests):
        raise ArchiveError("incomplete physical archive: orphan data or missing manifest")
    if not manifests and not allow_empty:
        raise ArchiveError("no sealed physical segment is present")
    expected = [names(i) for i in range(len(manifests))]
    if (data != {pair[0] for pair in expected}
            or manifests != {pair[1] for pair in expected}):
        raise ArchiveError("archive is missing its continuous sealed prefix")
    return range(len(manifests))


def verify_sealed_archive(directory, *, external_expected_head):
    """Prove a COMPLETE sealed prefix, explicitly anchored outside archive."""
    root = private_directory(directory)
    if not _digest(external_expected_head):
        raise ArchiveError("independent trusted sealed-prefix SHA256 required")
    index_range = _indices(root)
    previous = EMPTY_HEAD
    recorded_at_ns = -1
    expected_owner = None
    expected_size = None
    events = 0
    contains_private = False
    for index in index_range:
        m = read_manifest(root, index)
        if (expected_owner is not None and m["agent_id"] != expected_owner
                or expected_size is not None
                and m["segment_size"] != expected_size
                or m["prior_segment_head"] != previous):
            raise ArchiveError("invalid source identity or segment link")
        for event in iter_segment_events(root, m, previous):
            if event["recorded_at_ns"] < recorded_at_ns:
                raise ArchiveError("archive event time regresses")
            recorded_at_ns = event["recorded_at_ns"]
            events += 1
        previous = m["segment_head"]
        expected_owner = m["agent_id"]
        expected_size = m["segment_size"]
        contains_private |= m["contains_private"]
    if previous != external_expected_head:
        raise ArchiveError("sealed archive differs from external trusted anchor")
    return {
        "schema": SCHEMA, "agent_id": expected_owner,
        "segment_size": expected_size,
        "sealed_segments": len(index_range),
        "archived_events": events, "sealed_head": previous,
        "contains_private": contains_private,
        "trust": "externally-anchored-sealed-prefix-not-live-tail",
    }


def _require_unused_destination(path):
    for suffix in ("", "-wal", "-shm", "-journal"):
        member = path.with_name(path.name + suffix)
        if member.is_symlink() or member.exists():
            raise ArchiveError("restore requires a new absent SQLite path and no sidecars")


def restore_into_new_sqlite(directory, destination, *, external_expected_head):
    """Build a NEW independent SQLite copy from sealed physical archives.

    No replacement or in-place source migration. Verification completes
    *before* new destination publication, and imported data is rechecked
    against the pinned segment hashes. Incomplete live tail stays in source.
    """
    root = private_directory(directory)
    dest = Path(destination)
    if not dest.name:
        raise ArchiveError("restore requires a new absent local SQLite path")
    # A plaintext PRIVATE history must not be published into a parent
    # readable by other local users, even when source archive is 0700.
    private_directory(dest.parent)
    _require_unused_destination(dest)
    verified = verify_sealed_archive(
        root, external_expected_head=external_expected_head,
    )
    # Keep the scratch DB and any SQLite sidecars in one disposable directory.
    with tempfile.TemporaryDirectory(
        prefix=".mojum-segment-restore-", dir=dest.parent,
    ) as scratch:
        temp = Path(scratch) / "restore.sqlite3"
        previous = EMPTY_HEAD
        with SegmentedNoosphere(
            temp, verified["agent_id"],
            segment_size=verified["segment_size"],
        ) as journal:
            batch = []
            batch_bytes = 0
            for index in range(verified["sealed_segments"]):
                manifest = read_manifest(root, index)
                if (manifest["agent_id"] != verified["agent_id"]
                        or manifest["segment_size"] != verified["segment_size"]):
                    raise ArchiveError("archive identity changed during restore")
                for event, line_size in iter_segment_events(
                    root, manifest, previous, include_line_size=True,
                ):
                    if (len(batch) == MAX_BATCH
                            or batch_bytes + line_size > _RESTORE_BATCH_BYTES):
                        journal.append_many(batch, expected_head=journal.head())
                        batch.clear()
                        batch_bytes = 0
                    batch.append(event)
                    batch_bytes += line_size
                previous = manifest["segment_head"]
            if batch:
                journal.append_many(batch, expected_head=journal.head())
            report = journal.verify_chain(
                external_expected_head=external_expected_head
            )
            if (report["count"] != verified["archived_events"]
                    or report["segments"] != verified["sealed_segments"]):
                raise ArchiveError("reconstruction differed from verified prefix")
            # Materialize all committed WAL pages into the independent
            # scratch DB before we publish a one-file restored source.
            wal = journal._db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if wal is None or wal[0] != 0:
                raise ArchiveError("restored SQLite WAL checkpoint was blocked")
        leftover_wal = temp.with_name(temp.name + "-wal")
        if leftover_wal.is_file() and leftover_wal.stat().st_size:
            raise ArchiveError("restored SQLite has unapplied WAL pages")
        _require_unused_destination(dest)
        try:
            os.link(temp, dest)
        except FileExistsError as exc:
            raise ArchiveError("restore destination appeared; no overwrite") from exc
        # Once linked, the journal is live and another process may create
        # its sidecars and commit events. Do not roll back the published path.
        fsync_directory(dest.parent)
        return {
            "schema": SCHEMA, "restored_events": verified["archived_events"],
            "sealed_segments": verified["sealed_segments"],
            "sealed_head": verified["sealed_head"],
            "trust": "recovered-sealed-prefix-without-unarchived-tail",
        }
