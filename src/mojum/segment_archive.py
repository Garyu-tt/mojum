"""R0.17 PRIVATE physical archive of fully sealed logical segments.

Independent plaintext file + immutable manifest per full segment. No
automatic export of PRIVATE history; no migration/deletion/compaction.
SHA256 checks detect changes only against an independently trusted head.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import os
from pathlib import Path
import stat
import tempfile

from .noosphere import EMPTY_HEAD, _canonical
from .segmented_noosphere import SegmentedNoosphere

SCHEMA = "mojum.noosphere.physical-segment-archive/1"
MAX_SEGMENT_BYTES = 64 * 1024 * 1024


class ArchiveError(ValueError):
    """Missing, unsafe, incomplete, corrupted or incompatible archive."""


def private_directory(path):
    root = Path(path)
    if not root.is_dir() or root.is_symlink():
        raise ArchiveError("archive directory must exist and not be a symlink")
    if os.name == "posix" and (stat.S_IMODE(root.stat().st_mode) & 0o077):
        raise ArchiveError("archive directory must deny group/other access")
    return root


def names(index):
    prefix = "segment-%08d" % index
    return prefix + ".events.jsonl", prefix + ".manifest.json"


def fsync_directory(root):
    if os.name == "posix" and hasattr(os, "O_DIRECTORY"):
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def publish_new(path, blocks):
    """Atomic non-overwriting publication. Existing paths are NEVER replaced."""
    fd, temp = tempfile.mkstemp(prefix=".mojum-pending-", dir=path.parent)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as out:
            for block in blocks:
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(temp, path)
        except FileExistsError as exc:
            raise ArchiveError("refusing to replace already published archive") from exc
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
        fsync_directory(path.parent)


def export_sealed_segments(store, directory, *, include_nonpublic=False,
                           external_expected_head=None):
    """Export only complete sealed source segments to separate physical files.

    Explicit include_nonpublic=True is required: no PRIVATE data export
    by default. Unsealed tail stays exclusively in original SQLite.
    Existing archived segments are fully validated, never overwritten.
    """
    from .segment_archive_verify import _indices, verify_segment
    if type(store) is not SegmentedNoosphere:
        raise ArchiveError("export needs an explicit segmented Noosphere source")
    if include_nonpublic is not True:
        raise ArchiveError("PRIVATE data export requires explicit include_nonpublic=True")
    root = private_directory(directory)
    source = store.verify_chain(external_expected_head=external_expected_head)
    count = source["count"]
    sealed = count // store.segment_size
    archived = _indices(root, allow_empty=True)
    if len(archived) > sealed:
        raise ArchiveError("archive extends beyond the pinned source prefix")
    receipts = list(store.iter_segment_receipts(
        external_expected_head=source["head"]
    ))
    previous = EMPTY_HEAD
    copied = 0
    for index in range(sealed):
        receipt = receipts[index]
        if (receipt["events"] != store.segment_size
                or receipt["segment_index"] != index
                or receipt["prior_segment_head"] != previous):
            raise ArchiveError("source segment receipt or chain is incomplete")
        data_name, manifest_name = names(index)
        data_file, manifest_file = root / data_name, root / manifest_name
        if data_file.exists() or manifest_file.exists():
            if not data_file.exists() or not manifest_file.exists():
                raise ArchiveError("orphan/incomplete physical segment: quarantine manually")
            verified = verify_segment(
                root, index, prior=previous, expected_agent=store.agent_id,
                expected_size=store.segment_size,
            )
            if verified["segment_head"] != receipt["segment_head"]:
                raise ArchiveError("published immutable archive disagrees with source")
        else:
            start, end = receipt["first_seq"], receipt["last_seq"]
            sha = hashlib.sha256()
            written = 0
            contains_private = False
            cursor = store.iter_events(
                include_nonpublic=True, after_seq=start-1, batch_size=64
            )

            def blocks():
                nonlocal written, contains_private
                try:
                    for absolute in range(start, end+1):
                        try:
                            row = next(cursor)
                        except StopIteration as exc:
                            raise ArchiveError("missing source event") from exc
                        if row["seq"] != absolute:
                            raise ArchiveError("source events changed during export")
                        wire = (_canonical(row)+"\n").encode("utf-8")
                        written += len(wire)
                        if written > MAX_SEGMENT_BYTES:
                            raise ArchiveError("one segment exceeds 64MiB archive budget")
                        sha.update(wire)
                        contains_private |= row["event"]["visibility"] != "PUBLIC"
                        yield wire
                finally:
                    cursor.close()
            with closing(blocks()) as content:
                publish_new(data_file, content)
            manifest = {
                "schema": SCHEMA, "agent_id": store.agent_id,
                "segment_index": index, "segment_size": store.segment_size,
                "first_seq": start, "last_seq": end,
                "event_count": store.segment_size,
                "prior_segment_head": previous,
                "segment_head": receipt["segment_head"],
                "data_sha256": sha.hexdigest(),
                "data_bytes": written,
                "contains_private": contains_private,
                "encoding": "canonical-jsonl-utf8",
            }
            publish_new(manifest_file, [_canonical(manifest).encode("utf-8")])
            copied += 1
        previous = receipt["segment_head"]
    if store.head() != source["head"] or store.count() != count:
        raise ArchiveError("source journal advanced during export")
    return {
        "schema": SCHEMA, "copied_segments": copied,
        "sealed_segments": sealed, "archived_events": sealed*store.segment_size,
        "unsealed_source_events": count - sealed*store.segment_size,
        "sealed_head": previous, "source_head": source["head"],
        "source_events": count,
        "trust": "plaintext SHA256 needs independently pinned sealed_head",
    }
