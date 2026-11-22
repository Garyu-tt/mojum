"""CPU-only regression tests for real-operation span accounting."""
import concurrent.futures
from contextlib import closing
import os
import stat
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# The SQLite leak audit imports tests with PYTHONPATH=src, not the repo root.
# Make the in-repository tools package importable in both discovery modes.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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

    @unittest.skipUnless(os.name == "posix", "POSIX SQLite mode contract")
    def test_new_probe_db_wal_shm_are_owner_only_with_umask_022(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "private.sqlite3")
            previous = os.umask(0o022)
            try:
                with closing(probe.connect(path)) as db:
                    probe.initialize(db, "private-run")
                    probe.begin(db, "op", "worker", "model", now_ns=100)
                    for suffix in ("", "-wal", "-shm"):
                        item = Path(path + suffix)
                        self.assertTrue(item.is_file(), suffix)
                        self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600, suffix)
                self.assertEqual(os.umask(0o022), 0o022)
            finally:
                os.umask(previous)

    @unittest.skipUnless(os.name == "posix", "POSIX SQLite mode contract")
    def test_existing_readable_db_and_wal_fail_without_chmod(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "legacy.sqlite3")
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE old(x)")
                db.commit()
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(PermissionError, "owner-only"):
                probe.connect(path)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)
            os.chmod(path, 0o600)
            with closing(probe.connect(path)) as db:
                probe.initialize(db, "test-run")
                sidecar = path + "-wal"
                self.assertTrue(Path(sidecar).exists())
                os.chmod(sidecar, 0o644)
                with self.assertRaisesRegex(PermissionError, "owner-only"):
                    probe.connect(path)

    @unittest.skipUnless(os.name == "posix", "POSIX SQLite parent contract")
    def test_replaceable_parent_and_symlink_ancestor_fail_before_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            shared = Path(folder) / "shared"
            shared.mkdir()
            shared.chmod(0o777)
            path = shared / "private.sqlite3"
            with self.assertRaisesRegex(PermissionError, "replaceable"):
                probe.connect(path)
            self.assertEqual(probe.main(["--db", str(path), "report"]), 1)
            self.assertFalse(path.exists())
            real = Path(folder) / "real"
            real.mkdir(mode=0o700)
            alias = Path(folder) / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(PermissionError, "symlink"):
                probe.connect(alias / "private.sqlite3")
            self.assertFalse((real / "private.sqlite3").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX SQLite path contract")
    def test_memory_uri_and_db_symlink_are_not_persisted_evidence(self):
        for item in (":memory:", "file:private.sqlite3?mode=memory"):
            with self.assertRaisesRegex(ValueError, "persistent SQLite"):
                probe.connect(item)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "target.sqlite3"
            with closing(probe.connect(target)) as db:
                probe.initialize(db, "test-run")
            alias = Path(folder) / "alias.sqlite3"
            alias.symlink_to(target)
            with self.assertRaisesRegex(PermissionError, "owner-only"):
                probe.connect(alias)

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
