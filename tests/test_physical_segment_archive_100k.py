"""Real 102400-event 25-file sealed archive + independent SQLite restore.

Single high-volume CI job only. This is a data-layer test, NOT 102400
Aquarium motor ticks or proof of a two-device disaster-recovery system.
"""
import os
from pathlib import Path
import tempfile
import unittest

from mojum.segmented_noosphere import SegmentedNoosphere, MAX_BATCH
from mojum.segment_archive import export_sealed_segments
from mojum.segment_archive_verify import (
    restore_into_new_sqlite, verify_sealed_archive,
)
from test_segmented_noosphere import OWNER, ZERO, record


@unittest.skipUnless(os.environ.get("MOJUM_PHYSICAL_ARCHIVE_100K_STRESS") == "1",
                     "opt-in 102400-event archive stress test")
class Physical100KArchiveStress(unittest.TestCase):
    def test_complete_102400_event_prefix_survives_unavailable_source(self):
        total = 25 * 4096
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "synthetic-original.sqlite3"
            sealed = root / "PRIVATE-owner-only-archive"
            sealed.mkdir(mode=0o700)
            other = root / "restored-from-physical-files.sqlite3"
            with SegmentedNoosphere(db, OWNER, segment_size=4096) as source:
                head = ZERO
                for start in range(0, total, MAX_BATCH):
                    head = source.append_many([
                        record(n) for n in range(
                            start, min(start+MAX_BATCH, total)
                        )
                    ], expected_head=head)
                self.assertEqual(source.verify_chain()["count"], 102400)
                exported = export_sealed_segments(
                    source, sealed, include_nonpublic=True,
                    external_expected_head=head,
                )
                self.assertEqual(exported["copied_segments"], 25)
                self.assertEqual(exported["sealed_segments"], 25)
                self.assertEqual(exported["archived_events"], total)
                self.assertEqual(exported["unsealed_source_events"], 0)
                self.assertEqual(exported["sealed_head"], head)
                self.assertEqual(source.head(), head)
            # Simulate original source unavailable without removing any
            # real user data. Fixture is in disposable TemporaryDirectory.
            db.rename(root / "original-offline.sqlite3")
            report = verify_sealed_archive(
                sealed, external_expected_head=head,
            )
            self.assertEqual(report["archived_events"], total)
            self.assertEqual(report["sealed_segments"], 25)
            self.assertTrue(report["contains_private"])
            recovered = restore_into_new_sqlite(
                sealed, other, external_expected_head=head,
            )
            self.assertEqual(recovered["restored_events"], total)
            self.assertEqual(recovered["sealed_head"], head)
            with SegmentedNoosphere(other, OWNER, segment_size=4096) as q:
                self.assertEqual(
                    q.verify_chain(external_expected_head=head)["segments"],
                    25,
                )
                self.assertEqual(q.head(), head)
                self.assertEqual(
                    q.event_at(total)["event"]["payload"]["sequence"],
                    total-1,
                )
                # Export and restore are opt-in and the archived original
                # payloads are NEVER disclosed by the default projection.
                self.assertEqual(
                    q.dataset(limit=2)["projection"]["items"], [],
                )


if __name__ == "__main__":
    unittest.main()
