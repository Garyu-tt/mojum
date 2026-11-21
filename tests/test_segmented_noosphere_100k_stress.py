"""R0.16 actual 100,001 PRIVATE synthetic events across logical segments.

One matrix job only: SQLite FULL WAL and ~400 durable batch commits, then
streaming chain verification. This is an actual disk journal workload,
not invented numerical capacity metadata; no real user records.
"""
from pathlib import Path
import tempfile
import unittest

from mojum.noosphere import NoosphereError
from mojum.segmented_noosphere import SegmentedNoosphere, MAX_BATCH
from test_segmented_noosphere import OWNER, ZERO, record


class Beyond100KActualSQLiteTest(unittest.TestCase):
    def test_100001_durable_frames_across_25_explicit_linked_segments(self):
        count = 100_001
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "PRIVATE-ONLY-100001.sqlite3"
            with SegmentedNoosphere(path, OWNER, segment_size=4096) as log:
                tip = ZERO
                for start in range(0, count, MAX_BATCH):
                    tip = log.append_many(
                        [record(i) for i in range(
                            start, min(count, start + MAX_BATCH)
                        )],
                        expected_head=tip,
                    )
                self.assertEqual(log.count(), 100_001)
                self.assertEqual(log.head(), tip)
                self.assertEqual(
                    log.event_at(100_001)["event"]["payload"]["sequence"],
                    100_000,
                )
                self.assertEqual(
                    [x["seq"] for x in log.iter_events(
                        include_nonpublic=True, after_seq=99_998,
                        batch_size=2,
                    )],
                    [99_999, 100_000, 100_001],
                )
                self.assertEqual(
                    log._db.execute("SELECT count(*) FROM segments").fetchone()[0],
                    25,
                )
            with SegmentedNoosphere(path, OWNER, segment_size=4096) as reopened:
                verified = reopened.verify_chain(external_expected_head=tip)
                self.assertEqual(verified["count"], 100_001)
                self.assertEqual(verified["segments"], 25)
                receipts = list(reopened.iter_segment_receipts(
                    external_expected_head=tip
                ))
                self.assertEqual(len(receipts), 25)
                self.assertEqual(receipts[0]["events"], 4096)
                self.assertEqual(receipts[-1]["last_seq"], 100_001)
                self.assertEqual(receipts[-1]["segment_head"], tip)
                self.assertEqual(reopened.head(), tip)
                self.assertEqual(
                    reopened.event_at(100_001)["event_hash"], tip
                )
                with self.assertRaises(NoosphereError):
                    reopened.verify_chain(external_expected_head="0"*64)
                # Last event +1 only; history and all 25 boundaries stay.
                after = reopened.append(record(100_001), expected_head=tip)
                self.assertNotEqual(after, tip)
                self.assertEqual(reopened.count(), 100_002)
                self.assertEqual(
                    reopened._db.execute(
                        "SELECT count(*) FROM segments").fetchone()[0], 25,
                )
                self.assertEqual(reopened.verify_chain()["head"], after)
                self.assertEqual(
                    reopened.dataset(limit=10)["projection"]["items"], [],
                    "PRIVATE never leaks through default dataset projection",
                )


if __name__ == "__main__":
    unittest.main()
