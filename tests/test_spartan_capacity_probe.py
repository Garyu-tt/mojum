"""CPU-only regression tests for real-operation span accounting."""
import concurrent.futures
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import spartan_capacity_probe as probe


class CapacityProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "metrics.sqlite3")
        self.patch = patch.object(probe, "_boot_id", return_value="same-linux-boot")
        self.patch.start()
        self.db = probe.connect(self.db_path)
        probe.initialize(self.db, "run-001")

    def tearDown(self):
        self.db.close()
        self.patch.stop()
        self.tmp.cleanup()

    def test_300_distinct_overlapping_successful_model_spans(self):
        for i in range(300):
            probe.begin(self.db, f"op-{i}", f"worker-{i}", "test-model", now_ns=1000 + i)
        for i in range(300):
            probe.finish(self.db, f"op-{i}", "success", input_tokens=10,
                         output_tokens=2, cost_microusd=7, now_ns=2000 + i)
        result = probe.report(self.db)
        self.assertEqual(result["peak_simultaneous_successful_workers"], 300)
        self.assertEqual(result["peak_successful_calls"], 300)
        self.assertEqual(result["cost_microusd_reported"], 2100)
        self.assertEqual(result["input_tokens_reported"], 3000)

    def test_sequential_spans_do_not_count_as_concurrent(self):
        probe.begin(self.db, "a", "worker1", "m", now_ns=100)
        probe.finish(self.db, "a", "success", now_ns=200)
        probe.begin(self.db, "b", "worker2", "m", now_ns=200)
        probe.finish(self.db, "b", "success", now_ns=300)
        result = probe.report(self.db)
        self.assertEqual(result["peak_simultaneous_successful_workers"], 1)

    def test_repeated_worker_is_not_two_workers(self):
        for op in ("a", "b"):
            probe.begin(self.db, op, "same-worker", "m", now_ns=100)
            probe.finish(self.db, op, "success", now_ns=200)
        result = probe.report(self.db)
        self.assertEqual(result["peak_successful_calls"], 2)
        self.assertEqual(result["peak_simultaneous_successful_workers"], 1)

    def test_failed_and_crashed_spans_not_successful(self):
        probe.begin(self.db, "a", "w1", "m", now_ns=100)
        probe.begin(self.db, "b", "w2", "m", now_ns=101)
        probe.finish(self.db, "a", "error", now_ns=200)
        result = probe.report(self.db)
        self.assertEqual(result["unfinished_operations_excluded_from_peak"], 1)
        self.assertEqual(result["peak_simultaneous_successful_workers"], 0)
        self.assertEqual(result["peak_completed_calls"], 1)

    def test_duplicate_begin_and_finish_fail_closed(self):
        probe.begin(self.db, "a", "w1", "m", now_ns=100)
        with self.assertRaises(sqlite3.IntegrityError):
            probe.begin(self.db, "a", "w1", "m", now_ns=101)
        with self.assertRaises(ValueError):
            probe.finish(self.db, "a", "success", now_ns=100)
        probe.finish(self.db, "a", "success", now_ns=110)
        with self.assertRaises(ValueError):
            probe.finish(self.db, "a", "success", now_ns=120)
        self.assertEqual(probe.report(self.db)["successful_operations"], 1)

    def test_bad_cost_tokens_and_names_fail_before_write(self):
        with self.assertRaises(ValueError):
            probe.begin(self.db, "invalid\nsecret", "w1", "m", now_ns=100)
        probe.begin(self.db, "a", "w1", "m", now_ns=100)
        for kw in ({"cost_microusd": -1}, {"input_tokens": True},
                   {"output_tokens": 10**15}):
            with self.assertRaises(ValueError):
                probe.finish(self.db, "a", "success", now_ns=200, **kw)
        self.assertEqual(probe.report(self.db)["unfinished_operations_excluded_from_peak"], 1)

    def test_reuse_across_reboot_or_run_is_rejected(self):
        with self.assertRaises(RuntimeError):
            probe.initialize(self.db, "different-run")
        with patch.object(probe, "_boot_id", return_value="rebooted"):
            with self.assertRaises(RuntimeError):
                probe.begin(self.db, "a", "w", "m", now_ns=100)
            with self.assertRaises(RuntimeError):
                probe.report(self.db)

    def test_parallel_sqlite_process_like_writers(self):
        def emit(i):
            with closing(probe.connect(self.db_path)) as db:
                probe.begin(db, f"op-{i}", f"worker-{i}", "provider", now_ns=1000 + i)
                probe.finish(db, f"op-{i}", "success", now_ns=2000 + i)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(emit, range(32)))
        result = probe.report(self.db)
        self.assertEqual(result["completed_operations"], 32)
        self.assertEqual(result["peak_simultaneous_successful_workers"], 32)

    def test_report_threshold_is_conservative(self):
        probe.begin(self.db, "a", "w", "m", now_ns=100)
        probe.finish(self.db, "a", "success", now_ns=200)
        self.assertEqual(probe.report(self.db)["peak_simultaneous_successful_workers"], 1)
        self.assertEqual(probe.main(["--db", self.db_path, "report",
                                     "--min-simultaneous-successful-workers", "2"]), 2)


if __name__ == "__main__":
    unittest.main()
