"""Credential-free acceptance tests for bounded Spartan model spending."""

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from mojum.spartan_budget import BudgetCaps, BudgetDenied, BudgetLedger


class BudgetLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "budget.sqlite3"
        self.caps = BudgetCaps(2, 100, 300)
        self.ledger = BudgetLedger(self.path, self.caps)

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

    def test_clock_rollback_is_closed(self):
        self.ledger.reserve("a", "m", 1, 1, now_s=180)
        with self.assertRaises(BudgetDenied):
            self.ledger.reserve("b", "m", 1, 1, now_s=179)
        self.ledger.reserve("b", "m", 1, 1, now_s=180)

    def test_usage_is_not_refunded_and_overrun_freezes(self):
        self.ledger.reserve("a", "m", 40, 100, now_s=100)
        self.ledger.record_usage("a", 20, 3)
        self.ledger.record_usage("a", 20, 3)
        with self.assertRaises(BudgetDenied):
            self.ledger.record_usage("a", 19, 3)
        self.ledger.reserve("b", "m", 40, 100, now_s=101)
        with self.assertRaises(BudgetDenied):
            self.ledger.record_usage("b", 41, 101)
        with self.assertRaises(BudgetDenied):
            BudgetLedger(self.path, self.caps).reserve("c", "m", 1, 1, now_s=102)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT frozen FROM budget_settings").fetchone(), (1,))

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
