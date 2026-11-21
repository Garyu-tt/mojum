"""Credential-free Spartan budget tests; uses only Python stdlib."""
import concurrent.futures
from contextlib import closing
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import spartan_budget as budget


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "budget.sqlite3"
        self.db = budget.connect(self.path)
        budget.schema(self.db)
        budget.configure(self.db, 100, 900)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_first_reservation_allows_dispatch_once(self):
        one = budget.reserve(self.db, "request-1", 1, 30, 90)
        two = budget.reserve(self.db, "request-1", 1, 30, 90)
        self.assertTrue(one["dispatch_allowed"])
        self.assertFalse(two["dispatch_allowed"])
        self.assertEqual(budget.status(self.db)["debited_tokens"], 30)

    def test_immutable_configuration_and_restart(self):
        budget.configure(self.db, 100, 900)
        with self.assertRaisesRegex(budget.BudgetError, "immutable"):
            budget.configure(self.db, 1000, 900)
        budget.reserve(self.db, "call-1", 1, 40, 400)
        with closing(budget.connect(self.path)) as reopened:
            self.assertFalse(budget.reserve(reopened, "call-1", 1, 40, 400)["dispatch_allowed"])
            self.assertEqual(budget.status(reopened)["debited_micro_usd"], 400)

    def test_atomic_exhaustion_and_no_refund_after_settle(self):
        budget.reserve(self.db, "a", 1, 70, 700)
        with self.assertRaisesRegex(budget.BudgetError, "exhausted"):
            budget.reserve(self.db, "b", 2, 40, 100)
        budget.settle(self.db, "a", 1, 10, 1)
        with self.assertRaisesRegex(budget.BudgetError, "exhausted"):
            budget.reserve(self.db, "b", 2, 40, 100)
        self.assertEqual(budget.status(self.db)["requests"], 1)
        self.assertEqual(budget.status(self.db)["debited_tokens"], 70)
        self.assertEqual(budget.status(self.db)["reported_tokens"], 10)

    def test_timeout_ambiguity_reserves_permanently(self):
        budget.reserve(self.db, "timed-out", 1, 100, 900)
        with self.assertRaises(budget.BudgetError):
            budget.reserve(self.db, "retry-with-new-id", 1, 1, 0)
        self.assertFalse(budget.reserve(self.db, "timed-out", 1, 100, 900)["dispatch_allowed"])
        self.assertEqual(budget.status(self.db)["unsettled"], 1)

    def test_reused_id_changed_cost_or_worker_rejected(self):
        budget.reserve(self.db, "same", 1, 20, 90)
        for worker, tokens, dollars in ((2, 20, 90), (1, 21, 90), (1, 20, 91)):
            with self.assertRaisesRegex(budget.BudgetError, "reused"):
                budget.reserve(self.db, "same", worker, tokens, dollars)
        self.assertEqual(budget.status(self.db)["requests"], 1)

    def test_settlement_fenced_to_worker_and_exact_receipt(self):
        budget.reserve(self.db, "ticket", 1, 20, 40)
        with self.assertRaises(budget.BudgetError):
            budget.settle(self.db, "ticket", 2, 10, 2)
        with self.assertRaisesRegex(budget.BudgetError, "exceeds"):
            budget.settle(self.db, "ticket", 1, 21, 1)
        self.assertEqual(budget.status(self.db)["unsettled"], 1)
        self.assertTrue(budget.settle(self.db, "ticket", 1, 10, 2)["new_settlement"])
        self.assertFalse(budget.settle(self.db, "ticket", 1, 10, 2)["new_settlement"])
        with self.assertRaisesRegex(budget.BudgetError, "changed"):
            budget.settle(self.db, "ticket", 1, 11, 2)

    def test_all_integer_and_id_inputs_are_strict(self):
        for val in (True, 1.0, "1", -1, budget.MAX_VALUE + 1):
            with self.assertRaises(budget.BudgetError):
                budget.reserve(self.db, "id", 1, val, 0)
        for val in (0, 301, True, 1.0):
            with self.assertRaises(budget.BudgetError):
                budget.reserve(self.db, "id", val, 1, 0)
        for val in ("../escape", "bad id", "", "x" * 121, "тест", None):
            with self.assertRaises(budget.BudgetError):
                budget.reserve(self.db, val, 1, 1, 0)
        self.assertEqual(budget.status(self.db)["requests"], 0)

    def test_zero_micro_usd_supports_local_cpu(self):
        with closing(budget.connect(Path(self.temp.name) / "zero.sqlite3")) as db:
            budget.schema(db)
            budget.configure(db, 6, 0)
            self.assertTrue(budget.reserve(db, "local", 1, 6, 0)["dispatch_allowed"])
            self.assertEqual(budget.status(db)["debited_micro_usd"], 0)
            with self.assertRaises(budget.BudgetError):
                budget.reserve(db, "paid", 2, 1, 1)

    def test_concurrent_claims_never_exceed_cap(self):
        # Separate SQLite connections simulate 40 separate processes.
        def attempt(i):
            db = budget.connect(self.path)
            try:
                return budget.reserve(db, f"parallel-{i}", (i % 300) + 1, 5, 45)
            except budget.BudgetError:
                return None
            finally:
                db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            values = list(pool.map(attempt, range(40)))
        self.assertEqual(sum(x is not None and x["dispatch_allowed"] for x in values), 20)
        state = budget.status(self.db)
        self.assertEqual((state["debited_tokens"], state["debited_micro_usd"]), (100, 900))

    def test_cli_end_to_end(self):
        path = str(Path(self.temp.name) / "cli.sqlite3")
        cmd = [sys.executable, str(ROOT / "tools" / "spartan_budget.py"), "--db", path]
        def run(*args):
            return subprocess.run(cmd + list(args), capture_output=True, text=True)
        self.assertEqual(run("init", "--tokens", "3", "--micro-usd", "0").returncode, 0)
        a = run("reserve", "--id", "r", "--worker", "2", "--tokens", "2", "--micro-usd", "0")
        self.assertTrue(json.loads(a.stdout)["dispatch_allowed"])
        self.assertFalse(json.loads(run("reserve", "--id", "r", "--worker", "2", "--tokens", "2", "--micro-usd", "0").stdout)["dispatch_allowed"])
        self.assertEqual(run("reserve", "--id", "too-big", "--worker", "1", "--tokens", "2", "--micro-usd", "0").returncode, 2)
        self.assertEqual(run("settle", "--id", "r", "--worker", "2", "--actual-tokens", "1", "--actual-micro-usd", "0").returncode, 0)
        self.assertEqual(json.loads(run("status").stdout)["debited_tokens"], 2)


if __name__ == "__main__":
    unittest.main()
