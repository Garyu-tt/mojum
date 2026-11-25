"""Real sealed-archive receipts remain usable on non-UTF-8 consoles."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.segment_archive import export_sealed_segments
from mojum.segment_archive_verify import verify_sealed_archive
from mojum.segmented_noosphere import SegmentedNoosphere
from test_segmented_noosphere import ZERO, record


ROOT = Path(__file__).resolve().parents[1]
OWNER = "fixture-星-🦋"


class SegmentArchiveCLIStdoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "private-source.sqlite3"
        self.archive = self.root / "sealed-archive"
        self.archive.mkdir(mode=0o700)
        events = [record(index) for index in range(64)]
        for event in events:
            event["agent_id"] = OWNER
            event["payload"]["private_marker"] = "PRIVATE-PAYLOAD-NOT-A-RECEIPT"
        with SegmentedNoosphere(self.source, OWNER, segment_size=64) as journal:
            self.head = journal.append_many(events, expected_head=ZERO)
            export_sealed_segments(
                journal, self.archive, include_nonpublic=True,
                external_expected_head=self.head,
            )
        self.before = self.snapshot()

    def snapshot(self):
        return {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*") if path.is_file()
        }

    def verify(self, encoding, head):
        return subprocess.run(
            [
                sys.executable, "-B", "-m", "mojum.segment_archive_cli", "verify",
                "--archive-dir", str(self.archive),
                "--expected-sealed-head", head,
            ],
            cwd=ROOT,
            env=dict(os.environ, PYTHONPATH=str(ROOT / "src"),
                     PYTHONIOENCODING=encoding),
            capture_output=True, timeout=30,
        )

    def test_unicode_identity_receipt_roundtrips_on_narrow_stdout(self):
        expected = {"operation": "verify", **verify_sealed_archive(
            self.archive, external_expected_head=self.head,
        )}
        for encoding in ("ascii:strict", "cp1251:strict", "utf-8:strict"):
            with self.subTest(encoding=encoding):
                result = self.verify(encoding, self.head)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, b"")
                self.assertTrue(result.stdout.isascii())
                self.assertEqual(len(result.stdout.splitlines()), 1)
                receipt = json.loads(result.stdout)
                self.assertEqual(receipt, expected)
                self.assertEqual(receipt["agent_id"], OWNER)
                self.assertNotIn(b"PRIVATE-PAYLOAD-NOT-A-RECEIPT", result.stdout)
                self.assertNotIn(str(self.root), result.stdout.decode("ascii"))
                for member in self.archive.iterdir():
                    self.assertNotIn(member.name, result.stdout.decode("ascii"))
                self.assertEqual(self.snapshot(), self.before)

    def test_wrong_anchor_still_fails_without_a_receipt(self):
        for encoding in ("ascii:strict", "cp1251:strict", "utf-8:strict"):
            with self.subTest(encoding=encoding):
                result = self.verify(encoding, ZERO)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertNotIn(b"Traceback", result.stderr)
                self.assertEqual(self.snapshot(), self.before)


if __name__ == "__main__":
    unittest.main()
