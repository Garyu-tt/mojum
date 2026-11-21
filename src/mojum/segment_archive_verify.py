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

from .noosphere import EMPTY_HEAD as _ZERO, _canonical, _validate_event, NoosphereError
from .segmented_noosphere import (
    SegmentedNoosphere, _hash_event, MAX_SEGMENT_SIZE, MIN_SEGMENT_SIZE,
    MAX_BATCH,
)
from .segment_archive import (
    ArchiveError, SCHEMA, MAX_SEGMENT_BYTES, private_directory,
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
    except (ValueError, UnicodeError, TypeError, OverflowError, NoosphereError) as exc:
        raise ArchiveError("invalid canonical archive JSON") from exc


def _file(path, size_limit):
    if path.is_symlink():
        raise ArchiveError("symlink in sealed archive")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
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
            or type(m["event_count"]) is not int
            or m["event_count"] != m["segment_size"]
            or not _digest(m["prior_segment_head"])
            or not _digest(m["segment_head"])
            or not _digest(m["data_sha256"])
            or type(m["data_bytes"]) is not int
            or not 1 <= m["data_bytes"] <= MAX_SEGMENT_BYTES
            or type(m["contains_private"]) is not bool
            or m["encoding"] != "canonical-jsonl-utf8"):
        raise ArchiveError("invalid physical sealed segment manifest")
    return m


def iter_segment_events(root, manifest, previous):
    """Yield validated events, finish with full physical SHA/head verification.

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
    with _file(root / data_name, MAX_SEGMENT_BYTES) as source:
        while count < manifest["event_count"]:
            line = source.readline(131073)
            if (not line or len(line) > 131072 or not line.endswith(b"\n")):
                raise ArchiveError("missing/oversized physical event line")
            size += len(line)
            if size > MAX_SEGMENT_BYTES:
                raise ArchiveError("physical segment size budget exceeded")
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
            yield event
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


def _indices(root):
    manifests = sorted(root.glob("segment-*.manifest.json"))
    data = sorted(root.glob("segment-*.events.jsonl"))
    if len(manifests) > 100000 or len(data) > 100000:
        raise ArchiveError("archive member count exceeds bounded policy")
    if any(_META_RE.fullmatch(p.name) is None for p in manifests):
        raise ArchiveError("unexpected archive manifest name")
    if any(_DATA_RE.fullmatch(p.name) is None for p in data):
        raise ArchiveError("unexpected archive data filename")
    if len(data) != len(manifests):
        raise ArchiveError("orphan physical segment data or missing manifest")
    if not manifests:
        raise ArchiveError("no sealed physical segment is present")
    expected = [names(i) for i in range(len(manifests))]
    if ([p.name for p in data] != [pair[0] for pair in expected]
            or [p.name for p in manifests] != [pair[1] for pair in expected]):
        raise ArchiveError("archive is missing its continuous sealed prefix")
    return range(len(manifests))


def verify_sealed_archive(directory, *, external_expected_head):
    """Prove a COMPLETE sealed prefix, explicitly anchored outside archive."""
    root = private_directory(directory)
    if not _digest(external_expected_head):
        raise ArchiveError("independent trusted sealed-prefix SHA256 required")
    index_range = _indices(root)
    previous = _ZERO
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
        for _ in iter_segment_events(root, m, previous):
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


def restore_into_new_sqlite(directory, destination, *, external_expected_head):
    """Build a NEW independent SQLite copy from sealed physical archives.

    No replacement or in-place source migration. Verification completes
    *before* new destination publication, and imported data is rechecked
    against the pinned segment hashes. Incomplete live tail stays in source.
    """
    root = private_directory(directory)
    dest = Path(destination)
    if (dest.exists() or dest.is_symlink() or not dest.parent.is_dir()
            or dest.parent.is_symlink()):
        raise ArchiveError("restore requires a new absent local SQLite path")
    # A plaintext PRIVATE history must not be published into a parent
    # readable by other local users, even when source archive is 0700.
    private_directory(dest.parent)
    verified = verify_sealed_archive(
        root, external_expected_head=external_expected_head,
    )
    # Unique local scratch journal; publish only after complete reconstruction.
    fd, temp = tempfile.mkstemp(
        prefix=".mojum-segment-restore-", suffix=".sqlite3", dir=dest.parent,
    )
    os.close(fd)
    os.unlink(temp)  # SegmentedNoosphere requires a NEW path, no empty DB.
    try:
        previous = _ZERO
        with SegmentedNoosphere(
            temp, verified["agent_id"],
            segment_size=verified["segment_size"],
        ) as journal:
            batch = []
            for index in range(verified["sealed_segments"]):
                manifest = read_manifest(root, index)
                if (manifest["agent_id"] != verified["agent_id"]
                        or manifest["segment_size"] != verified["segment_size"]):
                    raise ArchiveError("archive identity changed during restore")
                for event in iter_segment_events(root, manifest, previous):
                    batch.append(event)
                    if len(batch) == MAX_BATCH:
                        journal.append_many(batch, expected_head=journal.head())
                        batch.clear()
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
        leftover_wal = Path(temp + "-wal")
        if leftover_wal.is_file() and leftover_wal.stat().st_size:
            raise ArchiveError("restored SQLite has unapplied WAL pages")
        if dest.exists() or dest.is_symlink():
            raise ArchiveError("restore destination appeared during import")
        try:
            os.link(temp, dest)
        except FileExistsError as exc:
            raise ArchiveError("restore destination appeared; no overwrite") from exc
        fsync_directory(dest.parent)
        return {
            "schema": SCHEMA, "restored_events": verified["archived_events"],
            "sealed_segments": verified["sealed_segments"],
            "sealed_head": verified["sealed_head"],
            "trust": "recovered-sealed-prefix-without-unarchived-tail",
        }
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
        fsync_directory(dest.parent)
