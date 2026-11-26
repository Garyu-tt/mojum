"""Physical archive verification must enforce journal-wide event identity.

All archives are exported from disposable synthetic SQLite journals. Corrupt
fixtures retain canonical JSON, valid hash chains and matching external anchors
so a rejection must come from the event contract, not accidental hash damage.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.noosphere import _canonical
from mojum.segment_archive import ArchiveError, export_sealed_segments, names
from mojum.segment_archive_verify import (
    restore_into_new_sqlite,
    verify_sealed_archive,
)
from mojum.segmented_noosphere import SegmentedNoosphere, _hash_event
from test_segmented_noosphere import OWNER, ZERO, record


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ID = "synthetic-private-identity-do-not-disclose-\U0001f680"


class PhysicalArchiveDuplicateIdentityTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.source = self.root / "source.sqlite3"
        self.archive = self.root / "archive"
        self.archive.mkdir(mode=0o700)
        self.restored = self.root / "restored.sqlite3"

    def _build_archive(self, count, *, identifiers=None):
        overrides = {0: PRIVATE_ID} if identifiers is None else identifiers
        with SegmentedNoosphere(self.source, OWNER, segment_size=64) as journal:
            head = journal.append_many(
                [record(n, id_override=overrides.get(n)) for n in range(count)],
                expected_head=ZERO,
            )
            result = export_sealed_segments(
                journal, self.archive, include_nonpublic=True,
                external_expected_head=head,
            )
        self.assertEqual(result["archived_events"], count)
        return result["sealed_head"]

    def _duplicate_first_id(self, target_sequence):
        """Rehash a controlled invalid event identity into an otherwise valid log."""
        manifests = sorted(self.archive.glob("*.manifest.json"))
        all_rows = []
        for index in range(len(manifests)):
            data_name, _ = names(index)
            all_rows.extend(json.loads(line) for line in
                            (self.archive / data_name).read_bytes().splitlines())
        all_rows[target_sequence - 1]["event"]["event_id"] = (
            all_rows[0]["event"]["event_id"]
        )
        previous = ZERO
        for index in range(len(manifests)):
            data_name, manifest_name = names(index)
            rows = all_rows[index * 64:(index + 1) * 64]
            prior_segment_head = previous
            for row in rows:
                row["event_hash"] = _hash_event(
                    row["event"], seq=row["seq"], segment_index=index,
                    previous=previous,
                )
                previous = row["event_hash"]
            data = "".join(_canonical(row) + "\n" for row in rows).encode("utf-8")
            (self.archive / data_name).write_bytes(data)
            manifest_path = self.archive / manifest_name
            manifest = json.loads(manifest_path.read_bytes())
            manifest.update(
                prior_segment_head=prior_segment_head,
                segment_head=previous,
                data_sha256=hashlib.sha256(data).hexdigest(),
                data_bytes=len(data),
            )
            manifest_path.write_text(_canonical(manifest), encoding="utf-8")
        return previous

    def _snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def _assert_duplicate_rejected(self, head):
        before = self._snapshot()
        with self.assertRaisesRegex(ArchiveError, "duplicate.*event|event.*duplicate") as caught:
            verify_sealed_archive(self.archive, external_expected_head=head)
        self.assertNotIn(PRIVATE_ID, str(caught.exception))
        self.assertEqual(self._snapshot(), before)

    def test_duplicate_inside_segment_is_rejected_despite_valid_hashes(self):
        original_head = self._build_archive(64)
        changed_head = self._duplicate_first_id(2)
        self.assertNotEqual(original_head, changed_head)
        self._assert_duplicate_rejected(changed_head)

    def test_duplicate_across_segments_is_rejected_despite_valid_hashes(self):
        original_head = self._build_archive(128)
        changed_head = self._duplicate_first_id(65)
        self.assertNotEqual(original_head, changed_head)
        self._assert_duplicate_rejected(changed_head)

    def test_restore_refuses_duplicate_before_staging_without_side_effects(self):
        self._build_archive(128)
        head = self._duplicate_first_id(128)
        before = self._snapshot()
        # Restore must reject during its initial verification, before creating
        # a reconstruction database. Its temporary publication lock is removed.
        with patch("mojum.segment_archive_verify.tempfile.TemporaryDirectory",
                   wraps=tempfile.TemporaryDirectory) as stage:
            with self.assertRaises(ValueError) as caught:
                restore_into_new_sqlite(
                    self.archive, self.restored, external_expected_head=head,
                )
        self.assertIsInstance(caught.exception, ArchiveError)
        self.assertRegex(str(caught.exception), "duplicate.*event|event.*duplicate")
        stage.assert_not_called()
        self.assertNotIn(PRIVATE_ID, str(caught.exception))
        self.assertFalse(self.restored.exists())
        self.assertEqual(self._snapshot(), before)

    def test_cli_verify_rejects_duplicate_without_success_receipt_or_private_id(self):
        self._build_archive(128)
        head = self._duplicate_first_id(65)
        before = self._snapshot()
        process = subprocess.run(
            [sys.executable, "-m", "mojum.segment_archive_cli", "verify",
             "--archive-dir", str(self.archive), "--expected-sealed-head", head],
            cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(process.stdout, "")
        self.assertRegex(process.stderr, "duplicate.*event|event.*duplicate")
        self.assertNotIn(PRIVATE_ID, process.stderr)
        self.assertNotIn("Traceback", process.stderr)
        self.assertEqual(self._snapshot(), before)

    def test_distinct_case_and_unicode_ids_verify_and_restore_exactly(self):
        identifiers = {
            0: "same-id", 1: "SAME-ID", 2: "caf\u00e9", 3: "cafe\u0301",
            64: "\u0434\u0430\u043d\u0442\u0435-\U0001f680", 127: "\U0001f680" * 256,
        }
        head = self._build_archive(128, identifiers=identifiers)
        before = self._snapshot()
        verified = verify_sealed_archive(self.archive, external_expected_head=head)
        self.assertEqual(verified["archived_events"], 128)
        self.assertEqual(verified["sealed_segments"], 2)
        self.assertEqual(self._snapshot(), before)
        restored = restore_into_new_sqlite(
            self.archive, self.restored, external_expected_head=head,
        )
        self.assertEqual(restored["restored_events"], 128)
        with SegmentedNoosphere(self.restored, OWNER, segment_size=64) as journal:
            self.assertEqual(journal.verify_chain(external_expected_head=head)["count"], 128)
            for index, identifier in identifiers.items():
                self.assertEqual(journal.event_at(index + 1)["event"]["event_id"], identifier)
        for name, content in before.items():
            self.assertEqual((self.root / name).read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
