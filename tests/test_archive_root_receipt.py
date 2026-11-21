"""Owner-HMAC physical archive receipts: independent digest and two copies.

No actual production private data, real cloud/device copies, or keys.
HMAC + a separately pinned receipt SHA prevents unsigned modification
and replay of a different receipt *when that external digest is trusted*.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from mojum.segmented_noosphere import SegmentedNoosphere
from mojum.segment_archive import (
    export_sealed_segments, ArchiveError, names,
)
from mojum.live_tail_capsule import capture_live_tail
from mojum.archive_root_receipt import (
    ReceiptError, sign_owner_receipt, verify_owner_receipt,
    verify_two_archive_copies,
)
from mojum.verified_snapshot import create_key_file
from test_segmented_noosphere import OWNER, ZERO, record


class VersionedArchiveReceiptQA(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root/"PRIVATE-synthetic.sqlite3"
        self.primary = self.root/"PRIVATE-copy-a"
        self.secondary = self.root/"PRIVATE-copy-b"
        self.receipts = self.root/"PRIVATE-owner-receipts"
        for path in (self.primary, self.secondary, self.receipts):
            path.mkdir(mode=0o700)
        self.receipt = self.receipts/"head-v1.receipt.json"
        self.key = self.root/"owner-HMAC-32bytes.key"
        self.wrong_key = self.root/"other-secret.key"
        create_key_file(self.key)
        create_key_file(self.wrong_key)

    def generate(self, count=130):
        with SegmentedNoosphere(
            self.db, OWNER, segment_size=64,
        ) as q:
            tip = ZERO
            for n in range(count):
                tip = q.append(record(n), expected_head=tip)
            export_sealed_segments(
                q, self.primary, include_nonpublic=True,
                external_expected_head=tip,
            )
            capture_live_tail(
                q, self.primary, include_nonpublic=True,
                external_expected_source_head=tip,
            )
        return tip

    def sign(self, tip, count=130):
        return sign_owner_receipt(
            self.primary, self.receipt, self.key, source_events=count,
            external_expected_source_head=tip,
        )

    def copy_to_secondary(self):
        for file in self.primary.iterdir():
            shutil.copy2(file, self.secondary/file.name)

    def test_two_verified_history_copies_and_owner_pinned_digest(self):
        tip = self.generate(130)
        sealed = self.sign(tip)
        self.copy_to_secondary()
        statement = verify_owner_receipt(
            self.receipt, self.key,
            independently_pinned_receipt_sha256=sealed["receipt_sha256"],
        )
        self.assertEqual(statement["source_events"], 130)
        self.assertEqual(statement["sealed_events"], 128)
        self.assertEqual(statement["tail_events"], 2)
        self.assertTrue(statement["contains_private"])
        self.assertEqual(statement["source_head"], tip)
        result = verify_two_archive_copies(
            self.primary, self.secondary, self.receipt, self.key,
            independently_pinned_receipt_sha256=sealed["receipt_sha256"],
        )
        self.assertEqual(result["copies_verified"], 2)
        self.assertEqual(result["source_head"], tip)
        self.assertIn("not-physical-redundancy", result["trust"])

    def test_wrong_key_wrong_pinned_root_and_modified_receipt_rejected(self):
        tip = self.generate(130)
        signed = self.sign(tip)
        with self.assertRaises(ReceiptError):
            verify_owner_receipt(
                self.receipt, self.wrong_key,
                independently_pinned_receipt_sha256=signed["receipt_sha256"],
            )
        with self.assertRaisesRegex(ReceiptError, "independent"):
            verify_owner_receipt(
                self.receipt, self.key,
                independently_pinned_receipt_sha256="e"*64,
            )
        with self.receipt.open("ab") as out:
            out.write(b"\n")
        with self.assertRaises(ReceiptError):
            verify_owner_receipt(
                self.receipt, self.key,
                independently_pinned_receipt_sha256=signed["receipt_sha256"],
            )

    def test_source_copy_modified_same_signed_head_refuses_recovery(self):
        tip = self.generate(130)
        signed = self.sign(tip)
        self.copy_to_secondary()
        data, _ = names(1)
        with (self.secondary/data).open("ab") as out:
            out.write(b"tamper\n")
        with self.assertRaises((ReceiptError, ArchiveError)):
            verify_two_archive_copies(
                self.primary, self.secondary, self.receipt, self.key,
                independently_pinned_receipt_sha256=signed["receipt_sha256"],
            )
        # The originally signed primary never becomes rewritten/erased.
        self.assertEqual(
            verify_owner_receipt(
                self.receipt, self.key,
                independently_pinned_receipt_sha256=signed["receipt_sha256"],
            )["source_head"], tip,
        )

    def test_new_path_and_trusted_external_head_required(self):
        tip = self.generate(130)
        with self.assertRaises((ReceiptError, ArchiveError)):
            sign_owner_receipt(
                self.primary, self.primary/"inside-archive.json",
                self.key, source_events=130,
                external_expected_source_head=tip,
            )
        self.assertFalse((self.primary/"inside-archive.json").exists())
        with self.assertRaises((ReceiptError, ArchiveError)):
            sign_owner_receipt(
                self.primary, self.receipt, self.key,
                source_events=130,
                external_expected_source_head="e"*64,
            )
        self.assertFalse(self.receipt.exists())
        signed = self.sign(tip)
        with self.assertRaisesRegex(ReceiptError, "new absent"):
            self.sign(tip)
        with self.assertRaisesRegex(ReceiptError, "distinct"):
            verify_two_archive_copies(
                self.primary, self.primary, self.receipt, self.key,
                independently_pinned_receipt_sha256=signed["receipt_sha256"],
            )

    def test_real_owner_receipt_cli_three_commands_no_payload_disclosure(self):
        head = self.generate(130)
        self.copy_to_secondary()
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=str(root / "src"))

        def cli(*args):
            return subprocess.run(
                [sys.executable, "-m", "mojum.segment_archive_cli", *map(str, args)],
                cwd=root, env=env, capture_output=True, text=True, timeout=20,
            )

        seal = cli(
            "seal-root", "--archive-dir", self.primary,
            "--receipt-file", self.receipt,
            "--key-file", self.key,
            "--source-events", "130", "--expected-source-head", head,
        )
        self.assertEqual(seal.returncode, 0, seal.stderr)
        sealed = json.loads(seal.stdout)
        self.assertEqual(sealed["source_events"], 130)
        self.assertNotIn("PRIVATE_SYNTHETIC", seal.stdout)
        verified = cli(
            "verify-root", "--receipt-file", self.receipt,
            "--key-file", self.key,
            "--expected-receipt-sha", sealed["receipt_sha256"],
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["source_head"], head)
        copies = cli(
            "verify-copies", "--archive-dir-a", self.primary,
            "--archive-dir-b", self.secondary,
            "--receipt-file", self.receipt, "--key-file", self.key,
            "--expected-receipt-sha", sealed["receipt_sha256"],
        )
        self.assertEqual(copies.returncode, 0, copies.stderr)
        self.assertEqual(json.loads(copies.stdout)["copies_verified"], 2)
        self.assertNotIn("PRIVATE_SYNTHETIC", copies.stdout)
        self.assertNotIn(str(self.key), copies.stdout)

    def test_signing_key_cannot_be_bundled_inside_plaintext_archive(self):
        tip = self.generate(130)
        unsafe_key = self.primary/"leaked-owner-key.bin"
        create_key_file(unsafe_key)
        with self.assertRaisesRegex(ReceiptError, "cannot be stored in archive"):
            sign_owner_receipt(
                self.primary, self.receipt, unsafe_key,
                source_events=130, external_expected_source_head=tip,
            )
        self.assertFalse(self.receipt.exists())

    def test_verification_receipt_cannot_be_embedded_with_second_copy(self):
        tip = self.generate(130)
        pinned = self.sign(tip)
        self.copy_to_secondary()
        bundled = self.secondary/"bundled-owner-receipt.json"
        shutil.copy2(self.receipt, bundled)
        with self.assertRaisesRegex(ReceiptError, "outside both archives"):
            verify_two_archive_copies(
                self.primary, self.secondary, bundled, self.key,
                independently_pinned_receipt_sha256=pinned["receipt_sha256"],
            )
        with self.assertRaisesRegex(ReceiptError, "outside both archives"):
            verify_two_archive_copies(
                self.primary, self.secondary, self.receipt,
                self.secondary/"signer-in-archive.key",
                independently_pinned_receipt_sha256=pinned["receipt_sha256"],
            )

    def test_genesis_only_tail_signing_recovery_and_no_private_payload_in_receipt(self):
        tip = self.generate(7)
        signed = self.sign(tip, count=7)
        raw = self.receipt.read_bytes()
        self.assertNotIn(b"PRIVATE_SYNTHETIC", raw)
        self.assertNotIn(b"synthetic-000000", raw)
        s = verify_owner_receipt(
            self.receipt, self.key,
            independently_pinned_receipt_sha256=signed["receipt_sha256"],
        )
        self.assertEqual(s["sealed_segments"], 0)
        self.assertEqual(s["tail_events"], 7)
        self.assertTrue(s["contains_private"])

    def test_missing_copy_or_alternate_history_cannot_pass_two_copy_check(self):
        tip = self.generate(130)
        signed = self.sign(tip)
        with self.assertRaises((ReceiptError, ArchiveError)):
            verify_two_archive_copies(
                self.primary, self.secondary, self.receipt, self.key,
                independently_pinned_receipt_sha256=signed["receipt_sha256"],
            )
        self.copy_to_secondary()
        data = self.secondary / "tail-000000000129-000000000130.events.jsonl"
        data.unlink()
        with self.assertRaises((ReceiptError, ArchiveError)):
            verify_two_archive_copies(
                self.primary, self.secondary, self.receipt, self.key,
                independently_pinned_receipt_sha256=signed["receipt_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
