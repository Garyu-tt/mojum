"""R0.20 owner-authorized OFFLINE copy of a verified PRIVATE point-in-time archive.

This is a synchronous file transfer to a pre-existing owner-only target.
By default source and target MUST have distinct VFS st_dev identifiers;
this is only a *necessary* distinction and DOES NOT establish independent
physical disks, hosts, power domains or real remote durability.

A separately pinned owner-HMAC receipt authenticates the exact files.
Nothing modifies the source, overwrites the destination, transports a
secret key, copies unselected newer tail capsules, or exports by default.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

from .segment_archive import (
    ArchiveError, MAX_SEGMENT_BYTES, names, private_directory, publish_new,
)
from .segment_archive_verify import _file
from .live_tail_capsule import tail_names
from .archive_root_receipt import (
    ReceiptError, verify_owner_receipt, verify_two_archive_copies,
)

SCHEMA = "mojum.noosphere.offline-archive-transfer/1"
CHUNK_SIZE = 128 * 1024


class TransferError(ValueError):
    """Unsafe custody, source mismatch or interrupted immutable copy."""


def _device_id(root):
    """VFS mount device ID. Separate IDs DO NOT prove independent hardware."""
    return root.stat().st_dev


def _members(statement):
    """Only the exact signed source prefix, not other local archived history."""
    parts = []
    for index in range(statement["sealed_segments"]):
        parts.extend(names(index))
    parts.extend(tail_names(
        statement["sealed_events"] + 1, statement["source_events"],
    ))
    return tuple(parts)


def _inside(sensitive_file, root):
    resolved = Path(sensitive_file).resolve()
    parent = root.resolve()
    return resolved == parent or resolved.is_relative_to(parent)


def _copy_blocks(source, byte_limit):
    """Yield strictly bounded chunks from an O_NOFOLLOW 0600 source file."""
    with _file(source, byte_limit) as stream:
        while True:
            data = stream.read(CHUNK_SIZE)
            if not data:
                break
            yield data


def copy_pinned_archive_to_device(
    source_directory, destination_directory, receipt_file, owner_key_file,
    *, independently_pinned_receipt_sha256, include_nonpublic=False,
):
    """Copy verified sealed prefix + EXACT pinned tail to another VFS device.

    Caller must explicitly approve PRIVATE plaintext export and separately
    provision an owner-only destination filesystem. Both the HMAC owner
    receipt and its independently pinned digest are REQUIRED. The receipt
    and key remain outside BOTH archive roots and are NEVER copied.

    Any partial destination (e.g., crash mid-transfer) is left untouched
    for manual quarantine, not rewritten or silently considered complete.
    """
    if include_nonpublic is not True:
        raise TransferError(
            "PRIVATE transfer requires explicit include_nonpublic=True"
        )
    if os.name != "posix":
        raise TransferError("distinct-device check requires POSIX filesystem")
    source = private_directory(source_directory)
    destination = private_directory(destination_directory)
    if source.resolve() == destination.resolve():
        raise TransferError("destination must be a different archive directory")
    if _device_id(source) == _device_id(destination):
        raise TransferError(
            "destination is on same filesystem device ID; not a separate VFS mount"
        )
    for path in (receipt_file, owner_key_file):
        if _inside(path, source) or _inside(path, destination):
            raise TransferError(
                "trusted owner key/receipt cannot be embedded in archive transfer"
            )

    statement = verify_owner_receipt(
        receipt_file, owner_key_file,
        independently_pinned_receipt_sha256=independently_pinned_receipt_sha256,
    )
    # The owner receipt must authenticate the ORIGINAL exact archive before
    # transferring a single byte (not just the metadata of destination).
    from .archive_root_receipt import _statement_for_archive
    verified_source = _statement_for_archive(
        source, source_events=statement["source_events"],
        external_expected_source_head=statement["source_head"],
    )
    if verified_source != statement:
        raise TransferError("source archive differs from signed owner receipt")

    selected = _members(statement)
    actual = {child.name for child in destination.iterdir()}
    expected = set(selected)
    if actual and actual != expected:
        raise TransferError(
            "destination contains incomplete or unrelated files; quarantine manually"
        )

    copied = 0
    copied_bytes = 0
    if not actual:
        for name in selected:
            source_file = source / name
            final = destination / name
            limit = (
                MAX_SEGMENT_BYTES
                if name.endswith(".events.jsonl") else 4096
            )
            # _file rejects symlinks, non-regular, non-0600 and oversize.
            # publish_new does not replace existing files and fsyncs.
            blocks = _copy_blocks(source_file, limit)
            publish_new(final, blocks)
            copied += 1
            copied_bytes += final.stat().st_size
    # Post-transfer: authenticate all source and target member data again.
    report = verify_two_archive_copies(
        source, destination, receipt_file, owner_key_file,
        independently_pinned_receipt_sha256=independently_pinned_receipt_sha256,
    )
    if report["source_head"] != statement["source_head"]:
        raise TransferError("postcopy root differs from signed source")
    return {
        "schema": SCHEMA,
        "source_events": statement["source_events"],
        "source_head": statement["source_head"],
        "files_copied": copied,
        "data_bytes_copied": copied_bytes,
        "archived_members": len(selected),
        "copies_verified": report["copies_verified"],
        "distinct_vfs_device_ids": True,
        "trust": "owner-hmac-verified-not-proof-of-independent-physical-devices",
    }
