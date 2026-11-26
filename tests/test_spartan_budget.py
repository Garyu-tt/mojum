"""Credential-free acceptance tests for bounded Spartan model spending."""

from contextlib import closing
import os
import stat
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from mojum.spartan_budget import BudgetCaps, BudgetDenied, BudgetLedger


class BudgetLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "budget.sqlite3"
        self.caps = BudgetCaps(2, 100, 300)
        self.ledger = BudgetLedger(self.path, self.caps)

    @unittest.skipUnless(os.name == "posix" and getattr(os, "O_DIRECTORY", 0)
                         and getattr(os, "O_NOFOLLOW", 0),
                         "POSIX inode and directory fsync required")
    def test_new_ledger_fsyncs_inode_then_private_parent(self):
        path = Path(self.tmp.name) / "fresh-fsync.sqlite3"
        original = os.fsync
        calls = []

        def trace(fd):
            mode = os.fstat(fd).st_mode
            calls.append("file" if stat.S_ISREG(mode) else
                         "directory" if stat.S_ISDIR(mode) else "other")
            return original(fd)

        with patch("mojum.spartan_budget.os.fsync", side_effect=trace):
            BudgetLedger(path, self.caps)
        self.assertEqual(calls, ["file", "directory"])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

        calls.clear()
        with patch("mojum.spartan_budget.os.fsync", side_effect=trace):
            BudgetLedger(path, self.caps)
        self.assertEqual(calls, ["directory"])

    @unittest.skipUnless(os.name == "posix" and getattr(os, "O_DIRECTORY", 0)
                         and getattr(os, "O_NOFOLLOW", 0),
                         "POSIX inode and directory fsync required")
    def test_ledger_sync_failure_prevents_initialization(self):
        for stage in ("file", "directory"):
            with self.subTest(stage=stage):
                path = Path(self.tmp.name) / f"fail-{stage}.sqlite3"
                original = os.fsync

                def fail(fd):
                    mode = os.fstat(fd).st_mode
                    if (stage == "file" and stat.S_ISREG(mode)
                            or stage == "directory" and stat.S_ISDIR(mode)):
                        raise OSError("injected fsync failure")
                    return original(fd)

                with patch("mojum.spartan_budget.os.fsync", side_effect=fail):
                    with self.assertRaisesRegex(OSError, "injected fsync failure"):
                        BudgetLedger(path, self.caps)
                self.assertTrue(path.exists())
                # A subsequent secure reopen retries the private directory
                # durability barrier rather than relying on a stale claim.
                recovered = BudgetLedger(path, self.caps)
                self.assertFalse(recovered.reserve(
                    f"recover-{stage}", "m", 1, 1, now_s=1).duplicate)

    @unittest.skipUnless(os.name == "posix", "POSIX filesystem ownership/modes")
    def test_new_ledger_and_wal_shm_are_owner_only_under_umask_022(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private-budget.sqlite3"
            previous = os.umask(0o022)
            try:
                ledger = BudgetLedger(path, self.caps)
                with closing(ledger._connect()) as db:
                    db.execute("BEGIN IMMEDIATE")
                    for suffix in ("", "-wal", "-shm"):
                        item = Path(str(path) + suffix)
                        self.assertTrue(item.exists(), suffix)
                        self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600)
                    db.rollback()
                self.assertEqual(os.umask(0o022), 0o022)
            finally:
                os.umask(previous)

    @unittest.skipUnless(os.name == "posix", "POSIX filesystem ownership/modes")
    def test_unsafe_existing_ledger_and_sidecar_fail_closed(self):
        os.chmod(self.path, 0o644)
        with self.assertRaisesRegex(BudgetDenied, "owner-only"):
            self.ledger.reserve("unsafe-db", "m", 1, 1, now_s=1)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o644)
        os.chmod(self.path, 0o600)
        sidecar = Path(str(self.path) + "-wal")
        sidecar.write_bytes(b"untrusted")
        os.chmod(sidecar, 0o644)
        with self.assertRaisesRegex(BudgetDenied, "owner-only"):
            self.ledger.reserve("unsafe-wal", "m", 1, 1, now_s=1)
        self.assertEqual(sidecar.read_bytes(), b"untrusted")

    @unittest.skipUnless(os.name == "posix", "POSIX filesystem ownership/modes")
    def test_symlink_ledger_or_parent_is_rejected(self):
        actual = self.path.with_name("actual.sqlite3")
        self.path.replace(actual)
        self.path.symlink_to(actual)
        with self.assertRaisesRegex(BudgetDenied, "owner-only"):
            self.ledger.reserve("symlink-db", "m", 1, 1, now_s=1)
        self.path.unlink()
        actual.replace(self.path)
        alias = Path(self.tmp.name) / "alias"
        real = Path(self.tmp.name) / "real"
        real.mkdir(mode=0o700)
        alias.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(BudgetDenied, "directory chain"):
            BudgetLedger(alias / "budget.sqlite3", self.caps)
        self.assertFalse((real / "budget.sqlite3").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX filesystem ownership/modes")
    def test_writable_parent_and_ancestor_refuse_file_creation(self):
        shared = Path(self.tmp.name) / "shared"
        shared.mkdir()
        os.chmod(shared, 0o777)
        with self.assertRaisesRegex(BudgetDenied, "directory chain"):
            BudgetLedger(shared / "budget.sqlite3", self.caps)
        private = shared / "private"
        private.mkdir(mode=0o700)
        with self.assertRaisesRegex(BudgetDenied, "directory chain"):
            BudgetLedger(private / "budget.sqlite3", self.caps)
        self.assertFalse((private / "budget.sqlite3").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX hardlink semantics")
    def test_hardlinked_db_and_sidecar_are_rejected_without_mutation(self):
        duplicate = self.path.with_name("linked-budget.sqlite3")
        os.link(self.path, duplicate)
        try:
            with self.assertRaisesRegex(BudgetDenied, "owner-only"):
                self.ledger.reserve("hardlinked-db", "m", 1, 1, now_s=1)
        finally:
            duplicate.unlink()

        sidecar = Path(str(self.path) + "-wal")
        sidecar.write_bytes(b"preexisting-sidecar")
        duplicate = self.path.with_name("linked-budget-wal")
        os.link(sidecar, duplicate)
        try:
            with self.assertRaisesRegex(BudgetDenied, "owner-only"):
                self.ledger.reserve("hardlinked-wal", "m", 1, 1, now_s=1)
            self.assertEqual(sidecar.read_bytes(), b"preexisting-sidecar")
        finally:
            duplicate.unlink()
            sidecar.unlink()

    @unittest.skipUnless(os.name == "posix", "POSIX absolute path contract")
    def test_relative_path_and_world_searchable_parent_are_rejected(self):
        with self.assertRaisesRegex(BudgetDenied, "absolute"):
            BudgetLedger("relative-budget.sqlite3", self.caps)
        parent = Path(self.tmp.name) / "world-searchable"
        parent.mkdir(mode=0o700)
        os.chmod(parent, 0o755)
        target = parent / "ledger.sqlite3"
        with self.assertRaisesRegex(BudgetDenied, "directory chain"):
            BudgetLedger(target, self.caps)
        self.assertFalse(target.exists())

    def test_memory_or_uri_ledger_path_is_refused(self):
        for bad in (":memory:", "file:budget.sqlite3", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                BudgetLedger(bad, self.caps)

    def test_atomic_rate_token_and_daily_caps(self):
        self.ledger.reserve("a", "local-20b", 50, 100, now_s=1000)
        self.ledger.reserve("b", "local-20b", 50, 100, now_s=1000)
        with self.assertRaises(BudgetDenied):
            self.ledger.reserve("c", "local-20b", 1, 1, now_s=1000)
        with self.assertRaises(BudgetDenied):
            self.ledger.reserve("d", "local-20b", 101, 1, now_s=1060)
        with self.assertRaises(BudgetDenied):
            self.ledger.reserve("e", "local-20b", 20, 101, now_s=1060)
        self.ledger.reserve("f", "local-20b", 20, 100, now_s=1060)
        with self.assertRaises(BudgetDenied):
            self.ledger.reserve("g", "local-20b", 1, 1, now_s=1060)
        self.ledger.reserve("h", "local-20b", 1, 10, now_s=86401)

    def test_idempotent_duplicate_uses_no_more_budget(self):
        first = self.ledger.reserve("stable", "m", 100, 300, now_s=120)
        again = self.ledger.reserve("stable", "m", 100, 300, now_s=121)
        self.assertFalse(first.duplicate)
        self.assertTrue(again.duplicate)
        self.assertEqual(first.request_digest, again.request_digest)
        with self.assertRaises(BudgetDenied):
            self.ledger.reserve("stable", "m", 99, 300, now_s=122)
        with self.assertRaises(BudgetDenied):
            self.ledger.reserve("other", "m", 1, 1, now_s=122)

    def test_reopening_cannot_reset_limits_or_change_caps(self):
        self.ledger.reserve("a", "m", 100, 300, now_s=200)
        other = BudgetLedger(self.path, self.caps)
        with self.assertRaises(BudgetDenied):
            other.reserve("b", "m", 1, 1, now_s=201)
        with self.assertRaises(ValueError):
            BudgetLedger(self.path, BudgetCaps(999, 999, 999))

    def test_mutating_public_caps_cannot_bypass_persisted_policy(self):
        self.ledger.reserve("first", "m", 50, 100, now_s=120)
        self.ledger.reserve("second", "m", 50, 100, now_s=120)
        # A caller can assign a different object even though BudgetCaps is
        # frozen. That must not alter SQLite's persisted quota contract.
        self.ledger.caps = BudgetCaps(999, 10_000, 10_000)
        with self.assertRaisesRegex(BudgetDenied, "caps mismatch"):
            self.ledger.reserve("bypass", "m", 1, 1, now_s=120)
        with self.assertRaisesRegex(BudgetDenied, "caps mismatch"):
            self.ledger.reserve("first", "m", 50, 100, now_s=120)
        reopened = BudgetLedger(self.path, self.caps)
        with self.assertRaisesRegex(BudgetDenied, "rate or daily"):
            reopened.reserve("bypass", "m", 1, 1, now_s=120)

    def test_clock_rollback_is_closed(self):
        self.ledger.reserve("a", "m", 1, 1, now_s=180)
        with self.assertRaises(BudgetDenied):
            self.ledger.reserve("b", "m", 1, 1, now_s=179)
        self.ledger.reserve("b", "m", 1, 1, now_s=180)

    def test_usage_is_not_refunded_and_overrun_freezes(self):
        self.ledger.reserve("a", "m", 40, 100, now_s=100)
        self.ledger.record_usage("a", 20, 3)
        self.ledger.record_usage("a", 20, 3)
        self.ledger.reserve("b", "m", 40, 100, now_s=101)
        with self.assertRaises(BudgetDenied):
            self.ledger.record_usage("b", 41, 101)
        with self.assertRaises(BudgetDenied):
            BudgetLedger(self.path, self.caps).reserve("c", "m", 1, 1, now_s=102)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT frozen FROM budget_settings").fetchone(), (1,))

    def test_conflicting_receipt_persistently_freezes_admission(self):
        self.ledger.reserve("first", "m", 40, 100, now_s=100)
        self.ledger.record_usage("first", 20, 3)
        self.ledger.record_usage("first", 20, 3)  # Same receipt is idempotent.
        with self.assertRaisesRegex(BudgetDenied, "conflicting provider usage"):
            self.ledger.record_usage("first", 21, 3)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(
                db.execute("SELECT frozen FROM budget_settings").fetchone(), (1,)
            )
        reopened = BudgetLedger(self.path, self.caps)
        with self.assertRaisesRegex(BudgetDenied, "frozen"):
            reopened.reserve("second", "m", 1, 1, now_s=101)
        with self.assertRaisesRegex(BudgetDenied, "frozen"):
            reopened.reserve("first", "m", 40, 100, now_s=101)

    def test_parallel_same_minute_never_oversubscribes(self):
        errors = []
        accepted = []
        barrier = threading.Barrier(8)

        def worker(i):
            try:
                ledger = BudgetLedger(self.path, self.caps)
                barrier.wait(timeout=5)
                ledger.reserve(f"worker-{i}", "m", 50, 100, now_s=500)
                accepted.append(i)
            except BudgetDenied:
                pass
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(len(accepted), 2)

    def test_invalid_values_rejected_without_database_debit(self):
        for bad in (True, -1, 0, 1.2, "1"):
            with self.assertRaises(ValueError):
                self.ledger.reserve("bad", "m", bad, 1, now_s=60)
        with self.assertRaises(ValueError):
            self.ledger.reserve("bad", "m", 1, False, now_s=60)
        with self.assertRaises(ValueError):
            self.ledger.reserve("", "m", 1, 1, now_s=60)
        with self.assertRaises(ValueError):
            self.ledger.reserve("ok", "m", 1, 1, now_s=True)
        self.ledger.reserve("ok", "m", 1, 1, now_s=60)


if __name__ == "__main__":
    unittest.main()
