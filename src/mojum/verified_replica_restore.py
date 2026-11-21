"""R0.21 fail-closed cold recovery of one owner-authenticated archive copy.

The canonical journal is NEVER opened or modified, the recovered journal
must be a NEW absent owner-only SQLite path, and no PRIVATE export occurs
without an explicit local operator authorization.

A separately pinned receipt digest + owner HMAC proves equivalence to a
selected historical head, NOT independent device custody, freshness of
that head, public-key identity, encryption, or automatic writer failover.
"""
from __future__ import annotations

from pathlib import Path

from .archive_root_receipt import (
    ReceiptError, verify_owner_receipt, _statement_for_archive,
)
from .segment_archive import ArchiveError, private_directory
from .live_tail_capsule import restore_point_in_time
from .segmented_noosphere import SegmentedNoosphere

SCHEMA = "mojum.noosphere.owner-verified-replica-restore/1"


def _beneath(candidate, root):
    """Prevent publishing/reusing source archive or credential tree."""
    c = Path(candidate).resolve(strict=False)
    p = Path(root).resolve()
    return c == p or c.is_relative_to(p)


def recover_pinned_replica(
    replica_archive_directory, new_sqlite_path, receipt_file, owner_key_file,
    *, independently_pinned_receipt_sha256, expected_agent_id,
    allow_private_restore=False,
):
    """Recover a read-only historical replica into a NEW local PRIVATE SQLite.

    Before writing any PRIVATE event bytes:
      1. caller opt-in and explicit actor identity are checked;
      2. sealed+tail archive is reverified against HMAC receipt and
         separately pinned digest, without touching the original source;
      3. dest must be absent in owner-only directory outside archive tree;
      4. secret key and signed receipt must be outside the archive tree.

    Once reconstructed, the NEW journal is read-only verified again for
    absolute count, original event chain and exact HMAC-pinned source head.
    No merge, deletion, repair, replacement, writer lease or promotion.
    """
    if allow_private_restore is not True:
        raise ReceiptError(
            "PRIVATE replica restore requires explicit allow_private_restore=True"
        )
    if type(expected_agent_id) is not str or not expected_agent_id:
        raise ReceiptError("expected_agent_id must be an explicit nonempty actor ID")
    root = private_directory(replica_archive_directory)
    new_path = Path(new_sqlite_path)
    if (new_path.exists() or new_path.is_symlink() or new_path.is_dir()):
        raise ReceiptError("restored SQLite destination must be new and absent")
    private_directory(new_path.parent)
    if _beneath(new_path, root):
        raise ReceiptError("restore target cannot be inside archive tree")
    for sensitive in (receipt_file, owner_key_file):
        if _beneath(sensitive, root):
            raise ReceiptError(
                "owner HMAC key and receipt cannot be stored inside replica archive"
            )
    signed = verify_owner_receipt(
        receipt_file, owner_key_file,
        independently_pinned_receipt_sha256=independently_pinned_receipt_sha256,
    )
    if signed["agent_id"] != expected_agent_id:
        raise ReceiptError("signed archive belongs to a different actor")
    independently_verified = _statement_for_archive(
        root, source_events=signed["source_events"],
        external_expected_source_head=signed["source_head"],
    )
    if independently_verified != signed:
        raise ReceiptError("replica archive content differs from owner receipt")
    restored = restore_point_in_time(
        root, new_path, source_events=signed["source_events"],
        external_expected_source_head=signed["source_head"],
    )
    # A new restored journal has already been fully verified in its scratch
    # path before atomic publication. Reopen through the same versioned,
    # read-only-preflight-backed storage contract to verify postpublish.
    with SegmentedNoosphere(
        new_path, expected_agent_id, segment_size=signed["segment_size"],
    ) as recovered:
        after = recovered.verify_chain(
            external_expected_head=signed["source_head"]
        )
        if (after["count"] != signed["source_events"]
                or after["head"] != signed["source_head"]
                or after["segments"] != (
                    signed["sealed_segments"] + 1
                )):
            raise ReceiptError(
                "published restore differs from owner-authenticated archive"
            )
    if (restored["restored_events"] != signed["source_events"]
            or restored["restored_tail_events"] != signed["tail_events"]):
        raise ReceiptError("restored tail count differs from signed receipt")
    return {
        "schema": SCHEMA,
        "agent_id": expected_agent_id,
        "restored_events": signed["source_events"],
        "sealed_segments": signed["sealed_segments"],
        "restored_tail_events": signed["tail_events"],
        "restored_head": signed["source_head"],
        "trust": "owner-hmac-pinned-historical-restore-not-writer-promotion",
    }
