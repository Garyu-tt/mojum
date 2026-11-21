"""Actual >100k PRIVATE SQLite source + sealed-prefix and live-tail restore.

This is CPU-only data-layer reproducibility, not an actual 100001-tick
brain/Aquarium or two independent real hardware failure domains.
"""
from pathlib import Path
import tempfile
import unittest

from mojum.segmented_noosphere import SegmentedNoosphere, MAX_BATCH
from mojum.segment_archive import export_sealed_segments
from mojum.live_tail_capsule import (
    capture_live_tail, verify_point_in_time, restore_point_in_time,
)
from test_segmented_noosphere import OWNER, ZERO, record


class Real100001EventPointInTimeQA(unittest.TestCase):
    def test_100001_events_24_sealed_segments_plus_1697_live_tail(self):
        target = 100_001
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "PRIVATE-synthetic.sqlite3"
            archive = root / "PRIVATE-physical-archive"
            archive.mkdir(mode=0o700)
            recovered = root / "independent-restored.sqlite3"
            with SegmentedNoosphere(source, OWNER, segment_size=4096) as log:
                tip = ZERO
                for begin in range(0, target, MAX_BATCH):
                    tip = log.append_many(
                        [record(i) for i in range(
                            begin, min(begin + MAX_BATCH, target)
                        )], expected_head=tip,
                    )
                self.assertEqual(
                    log.verify_chain(external_expected_head=tip)["count"], target,
                )
                sealed = export_sealed_segments(
                    log, archive, include_nonpublic=True,
                    external_expected_head=tip,
                )
                self.assertEqual(sealed["sealed_segments"], 24)
                self.assertEqual(sealed["archived_events"], 98_304)
                self.assertEqual(sealed["unsealed_source_events"], 1697)
                tail = capture_live_tail(
                    log, archive, include_nonpublic=True,
                    external_expected_source_head=tip,
                )
                self.assertEqual(tail["sealed_segments"], 24)
                self.assertEqual(tail["tail_events"], 1697)
                self.assertEqual(tail["archived_events"], target)
                self.assertEqual(tail["source_head"], tip)
            # Physical archive verification and recovery from files ONLY;
            # the original disposable SQLite becomes unavailable.
            source.rename(root / "source-offline.sqlite3")
            verified = verify_point_in_time(
                archive, source_events=target,
                external_expected_source_head=tip,
            )
            self.assertEqual(verified["archived_events"], target)
            self.assertEqual(verified["sealed_events"], 98_304)
            self.assertEqual(verified["tail_events"], 1697)
            result = restore_point_in_time(
                archive, recovered, source_events=target,
                external_expected_source_head=tip,
            )
            self.assertEqual(result["restored_events"], target)
            self.assertEqual(result["restored_head"], tip)
            with SegmentedNoosphere(recovered, OWNER, segment_size=4096) as restored:
                report = restored.verify_chain(external_expected_head=tip)
                self.assertEqual(report["count"], target)
                self.assertEqual(report["segments"], 25)
                self.assertEqual(
                    restored.event_at(target)["event"]["payload"]["sequence"],
                    target - 1,
                )
                self.assertEqual(
                    restored.dataset(limit=5)["projection"]["items"], [],
                    "PRIVATE dataset must remain excluded by default",
                )


if __name__ == "__main__":
    unittest.main()
