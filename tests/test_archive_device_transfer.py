"""R0.20 HMAC-pinned offline archive copy, VFS separation and fail closed QA.

All PRIVATE content is synthetic in disposable owner-only folders.
Python unit tests mock VFS device IDs to simulate a separate mount for
copy-path testing. This is NOT a real two-device hardware validation.
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.segmented_noosphere import SegmentedNoosphere
from mojum.segment_archive import (
    export_sealed_segments, ArchiveError, names,
)
from mojum.live_tail_capsule import (
    capture_live_tail, tail_names, verify_point_in_time,
)
from mojum.archive_root_receipt import (
    sign_owner_receipt, verify_owner_receipt, ReceiptError,
)
from mojum.archive_device_transfer import (
    copy_pinned_archive_to_device, TransferError,
)
from mojum.verified_snapshot import create_key_file
from test_segmented_noosphere import OWNER, ZERO, record


class OfflinePhysicalArchiveTransferQA(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "PRIVATE-original.sqlite3"
        self.source = self.root / "source-archive"
        self.target = self.root / "destination-archive"
        self.keys = self.root / "owner-credentials"
        for folder in (self.source, self.target, self.keys):
            folder.mkdir(mode=0o700)
        self.key = self.keys / "owner-32-bytes.key"
        self.receipt = self.keys / "owner-checkpoint.json"
        create_key_file(self.key)
        self.head = None
        self.digest = None

    def prepare(self, count=130):
        with SegmentedNoosphere(self.db, OWNER, segment_size=64) as journal:
            tip = ZERO
            for i in range(count):
                tip = journal.append(record(i), expected_head=tip)
            export_sealed_segments(
                journal, self.source, include_nonpublic=True,
                external_expected_head=tip,
            )
            capture_live_tail(
                journal, self.source, include_nonpublic=True,
                external_expected_source_head=tip,
            )
        signed = sign_owner_receipt(
            self.source, self.receipt, self.key,
            source_events=count, external_expected_source_head=tip,
        )
        self.head = tip
        self.digest = signed["receipt_sha256"]

    def transfer(self, *, dest=None, root_hash=None, key=None):
        return copy_pinned_archive_to_device(
            self.source, self.target if dest is None else dest,
            self.receipt, self.key if key is None else key,
            independently_pinned_receipt_sha256=(
                self.digest if root_hash is None else root_hash
            ),
            include_nonpublic=True,
        )

    def different_vfs_mock(self):
        # Exercise real byte-copy/verification on tmpfs-like simulated device
        # IDs, NOT evidence of two independent physical filesystems.
        def classify(path):
            return 100 if Path(path).resolve() == self.source.resolve() else 200
        return patch("mojum.archive_device_transfer._device_id",
                     side_effect=classify)

    def test_default_real_same_device_refuses_before_any_copy(self):
        self.prepare()
        with self.assertRaisesRegex(TransferError, "same filesystem device"):
            self.transfer()
        self.assertEqual(list(self.target.iterdir()), [])
        with self.assertRaisesRegex(TransferError, "explicit"):
            copy_pinned_archive_to_device(
                self.source, self.target, self.receipt, self.key,
                independently_pinned_receipt_sha256=self.digest,
            )
        self.assertEqual(list(self.target.iterdir()), [])

    def test_verified_copy_and_idempotent_check_on_simulated_separate_vfs(self):
        self.prepare()
        with self.different_vfs_mock():
            copied = self.transfer()
            again = self.transfer()
        self.assertEqual(copied["source_events"], 130)
        self.assertEqual(copied["source_head"], self.head)
        self.assertEqual(copied["files_copied"], 6)
        self.assertGreater(copied["data_bytes_copied"], 0)
        self.assertEqual(copied["copies_verified"], 2)
        self.assertIn("not-proof-of-independent-physical", copied["trust"])
        self.assertEqual(again["files_copied"], 0)
        self.assertEqual(again["data_bytes_copied"], 0)
        original_files = {p.name: p.read_bytes() for p in self.source.iterdir()}
        target_files = {p.name: p.read_bytes() for p in self.target.iterdir()}
        self.assertEqual(target_files, original_files)
        self.assertNotIn(self.key.name, target_files)
        self.assertNotIn(self.receipt.name, target_files)

    def test_invalid_owner_receipt_rejected_without_copy(self):
        self.prepare()
        with self.different_vfs_mock():
            with self.assertRaises(ReceiptError):
                self.transfer(root_hash="a"*64)
        self.assertEqual(list(self.target.iterdir()), [])
        fake = self.keys / "different-owner.key"
        create_key_file(fake)
        with self.different_vfs_mock():
            with self.assertRaises(ReceiptError):
                self.transfer(key=fake)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_corrupt_source_fails_before_copy(self):
        self.prepare()
        data, _ = names(1)
        with (self.source / data).open("ab") as out:
            out.write(b"evil\n")
        with self.different_vfs_mock():
            with self.assertRaises((ArchiveError, ReceiptError, TransferError)):
                self.transfer()
        self.assertEqual(list(self.target.iterdir()), [])

    def test_interruption_leaves_quarantined_partial_copy(self):
        self.prepare()
        import mojum.archive_device_transfer as subject
        original = subject.publish_new
        count = []

        def interrupted(path, chunks):
            count.append(path)
            if len(count) == 2:
                raise RuntimeError("simulated copy interruption")
            return original(path, chunks)
        with self.different_vfs_mock():
            with patch.object(subject, "publish_new", side_effect=interrupted):
                with self.assertRaisesRegex(RuntimeError, "interruption"):
                    self.transfer()
            self.assertEqual(len(list(self.target.iterdir())), 1)
            with self.assertRaisesRegex(TransferError, "incomplete"):
                self.transfer()
        with SegmentedNoosphere(self.db, OWNER, segment_size=64) as journal:
            self.assertEqual(journal.head(), self.head)
            self.assertEqual(journal.count(), 130)

    def test_complete_but_tampered_target_never_overwritten(self):
        self.prepare()
        with self.different_vfs_mock():
            self.transfer()
            file = self.target / names(0)[0]
            with file.open("ab") as writer:
                writer.write(b"CORRUPTION\n")
            changed = file.read_bytes()
            with self.assertRaises((ArchiveError, ReceiptError, TransferError)):
                self.transfer()
            self.assertEqual(file.read_bytes(), changed)

    def test_unrelated_target_member_and_embedded_key_fail_closed(self):
        self.prepare()
        garbage = self.target/"unrelated-sensitive-file"
        garbage.write_text("keep", encoding="utf-8")
        with self.different_vfs_mock():
            with self.assertRaisesRegex(TransferError, "unrelated"):
                self.transfer()
        self.assertEqual(garbage.read_text(), "keep")
        garbage.unlink()
        with self.different_vfs_mock():
            # No new filename may be an owner signing key embedded in archive.
            with self.assertRaisesRegex(TransferError, "trusted"):
                copy_pinned_archive_to_device(
                    self.source, self.target, self.receipt,
                    self.source/"secret-in-source.key",
                    independently_pinned_receipt_sha256=self.digest,
                    include_nonpublic=True,
                )
        self.assertEqual(list(self.target.iterdir()), [])

    def test_genesis_only_tail_copies_two_files(self):
        self.prepare(count=7)
        with self.different_vfs_mock():
            result = self.transfer()
        self.assertEqual(result["archived_members"], 2)
        self.assertEqual(result["files_copied"], 2)
        history = verify_point_in_time(
            self.target, source_events=7,
            external_expected_source_head=self.head,
        )
        self.assertEqual(history["tail_events"], 7)
        self.assertEqual(history["sealed_segments"], 0)


if __name__ == "__main__":
    unittest.main()
