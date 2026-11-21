"""R0.21 fail-closed cold recovery of one owner-authenticated archive copy.

The canonical journal is NEVER opened or modified, the recovered journal
must be a NEW absent owner-only SQLite path, and no PRIVATE export occurs
without an explicit local operator authorization.

A separately pinned receipt digest + owner HMAC proves equivalence to a
selected historical head, NOT independent device custody, freshness of
that head, public-key identity, encryption, or automatic writer failover.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile

from .archive_root_receipt import (
    ReceiptError, verify_owner_receipt, _statement_for_archive,
)
from .segment_archive import ArchiveError, private_directory, fsync_directory
from .live_tail_capsule import restore_point_in_time
from .segmented_noosphere import SegmentedNoosphere

SCHEMA = "mojum.noosphere.owner-verified-replica-restore/1"
CUSTODY_SCHEMA = "mojum.noosphere.recovered-quarantine/1"


def _seal_read_only_custody(journal, owner, signed, receipt_sha):
    """Persist accidental-write fences INSIDE scratch SQLite before publication.

    These SQLite INSERT triggers defend against unintentional use by an
    older canonical writer. An OS owner/admin can deliberately DROP the
    triggers; this is NOT cryptographic writer fencing, quorum or lease.
    """
    before = journal.verify_chain(external_expected_head=signed["source_head"])
    if before["count"] != signed["source_events"]:
        raise ReceiptError("staged archive count differs from authenticated source")
    db = journal._db
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE recovery_custody (
            id INTEGER PRIMARY KEY CHECK(id=1),
            schema TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            source_events INTEGER NOT NULL,
            pinned_source_head TEXT NOT NULL,
            pinned_receipt_sha256 TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state='QUARANTINED_READ_ONLY')
        )""")
        db.execute(
            "INSERT INTO recovery_custody("
            "id,schema,agent_id,source_events,pinned_source_head,"
            "pinned_receipt_sha256,state) VALUES(1,?,?,?,?,?,?)",
            (CUSTODY_SCHEMA, owner, signed["source_events"],
             signed["source_head"], receipt_sha, "QUARANTINED_READ_ONLY"),
        )
        # Keep the recorded custody statement immutable under ordinary
        # application SQL as well as denying the normal journal writer.
        # A database administrator can still DROP these triggers: this is
        # accidental-write prevention, not Byzantine writer fencing.
        db.execute("""CREATE TRIGGER recovery_no_custody_update
            BEFORE UPDATE ON recovery_custody BEGIN
                SELECT RAISE(ABORT,'immutable recovered replica custody');
            END""")
        db.execute("""CREATE TRIGGER recovery_no_custody_delete
            BEFORE DELETE ON recovery_custody BEGIN
                SELECT RAISE(ABORT,'immutable recovered replica custody');
            END""")
        db.execute("""CREATE TRIGGER recovery_no_event_insert
            BEFORE INSERT ON segmented_events BEGIN
                SELECT RAISE(ABORT,'quarantined PRIVATE replica cannot accept writes');
            END""")
        db.execute("""CREATE TRIGGER recovery_no_segment_insert
            BEFORE INSERT ON segments BEGIN
                SELECT RAISE(ABORT,'quarantined PRIVATE replica cannot add segments');
            END""")
        db.execute("COMMIT")
        checkpoint = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or checkpoint[0] != 0:
            raise ReceiptError("recovered quarantine WAL checkpoint was blocked")
        row = db.execute(
            "SELECT schema,agent_id,source_events,pinned_source_head,"
            "pinned_receipt_sha256,state FROM recovery_custody WHERE id=1",
        ).fetchone()
        if (row is None or tuple(row) != (
                CUSTODY_SCHEMA, owner, signed["source_events"],
                signed["source_head"], receipt_sha, "QUARANTINED_READ_ONLY")):
            raise ReceiptError("cannot verify immutable quarantine custody marker")
    except sqlite3.Error as exc:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise ReceiptError("failed to seal recovery as read-only quarantine") from exc



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
    # Construct a NEW private staging path, reconstruct and verify fully,
    # then add write-rejecting SQLite triggers BEFORE publishing the output.
    # There is no window where the public final path is an unfenced writer.
    with tempfile.TemporaryDirectory(
        prefix=".mojum-verified-recovery-", dir=new_path.parent,
    ) as work:
        candidate = Path(work) / "quarantined-recovered.sqlite3"
        restored = restore_point_in_time(
            root, candidate, source_events=signed["source_events"],
            external_expected_source_head=signed["source_head"],
        )
        if (restored["restored_events"] != signed["source_events"]
                or restored["restored_tail_events"] != signed["tail_events"]):
            raise ReceiptError("restored tail count differs from signed receipt")
        with SegmentedNoosphere(
            candidate, expected_agent_id, segment_size=signed["segment_size"],
        ) as recovered:
            _seal_read_only_custody(
                recovered, expected_agent_id, signed,
                independently_pinned_receipt_sha256,
            )
            after = recovered.verify_chain(
                external_expected_head=signed["source_head"]
            )
            if (after["count"] != signed["source_events"]
                    or after["head"] != signed["source_head"]
                    or after["segments"] != signed["sealed_segments"] + 1):
                raise ReceiptError(
                    "quarantined recovery differs from authenticated archive"
                )
        wal = Path(str(candidate) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise ReceiptError("quarantined recovery has unapplied WAL bytes")
        if new_path.is_symlink() or new_path.exists():
            raise ReceiptError("restore target appeared during recovery")
        try:
            os.link(candidate, new_path)
        except FileExistsError as exc:
            raise ReceiptError("restore destination was created concurrently") from exc
        fsync_directory(new_path.parent)
    # The final file was atomically published ONLY after its triggers were
    # committed and checkpointed, and no original writer was touched.
    with SegmentedNoosphere(
        new_path, expected_agent_id, segment_size=signed["segment_size"],
    ) as published:
        confirmed = published.verify_chain(
            external_expected_head=signed["source_head"]
        )
        quarantine = published._db.execute(
            "SELECT state FROM recovery_custody WHERE id=1",
        ).fetchone()
        if (confirmed["count"] != signed["source_events"]
                or quarantine is None
                or quarantine["state"] != "QUARANTINED_READ_ONLY"):
            raise ReceiptError("published recovery quarantine failed validation")
    return {
        "schema": SCHEMA,
        "agent_id": expected_agent_id,
        "restored_events": signed["source_events"],
        "sealed_segments": signed["sealed_segments"],
        "restored_tail_events": signed["tail_events"],
        "restored_head": signed["source_head"],
        "custody": "QUARANTINED_READ_ONLY",
        "custody_schema": CUSTODY_SCHEMA,
        "trust": "owner-hmac-pinned-historical-restore-not-writer-promotion",
    }
