"""Atomic dispatcher tests: no LLM required."""
import concurrent.futures
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# The Python 3.13 SQLite leak audit is executed as ``python tests/...``.  In
# that mode Python puts ``tests/`` rather than the repository root on
# ``sys.path``, so repository-local command modules need the root explicitly.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tools.spartan_coordinator as coordinator
from tools.spartan_coordinator import (
    MAX_ATTEMPTS, claim, connect, create_schema, enqueue, finish, heartbeat,
    init, status,
)


class SpartanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "spartan.sqlite3")
        self.db = connect(self.path)
        create_schema(self.db)
        init(self.db, 300, 100)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_300_slots(self):
        self.assertEqual(status(self.db)["slots"], 300)

    def test_repeated_init_is_idempotent(self):
        init(self.db, 300, 101)
        self.assertEqual(status(self.db)["slots"], 300)

    def test_enqueue_idempotent(self):
        items = [{"id": "a", "scope": "room"}]
        self.assertEqual(enqueue(self.db, items, 101)["added"], 1)
        self.assertEqual(enqueue(self.db, items, 102)["added"], 0)

    def test_scope_exclusive_and_parallel_different_scopes(self):
        enqueue(self.db, [{"id": "a", "scope": "room"},
                          {"id": "b", "scope": "room"},
                          {"id": "c", "scope": "memory"}], 101)
        self.assertEqual(claim(self.db, 1, 110)["id"], "a")
        self.assertEqual(claim(self.db, 2, 110)["id"], "c")
        self.assertIsNone(claim(self.db, 3, 110))

    def test_same_worker_idempotent(self):
        enqueue(self.db, [{"id": "a", "scope": "room"},
                          {"id": "b", "scope": "memory"}], 101)
        self.assertEqual(claim(self.db, 1, 110)["id"], "a")
        self.assertEqual(claim(self.db, 1, 120)["id"], "a")

    def test_expiration_and_three_attempt_limit(self):
        enqueue(self.db, [{"id": "a", "scope": "room"}], 101)
        self.assertEqual(claim(self.db, 1, 110, 30)["attempts"], 1)
        self.assertEqual(claim(self.db, 2, 141, 30)["attempts"], 2)
        self.assertEqual(claim(self.db, 3, 172, 30)["attempts"], 3)
        self.assertIsNone(claim(self.db, 4, 203, 30))
        self.assertEqual(status(self.db)["states"]["blocked"], 1)

    def test_review_requires_artifact_and_no_self_accept(self):
        enqueue(self.db, [{"id": "a", "scope": "room"}], 101)
        claim(self.db, 1, 110)
        with self.assertRaises(ValueError):
            finish(self.db, 1, "a", "review", 115, attempt=1)
        with self.assertRaises(ValueError):
            finish(self.db, 1, "a", "done", 115, "PR#1", attempt=1)
        self.assertTrue(finish(self.db, 1, "a", "review", 115, "PR#1", attempt=1))
        self.assertFalse(finish(self.db, 1, "a", "blocked", 116, attempt=1))
        self.assertEqual(status(self.db)["states"]["review"], 1)

    def test_heartbeat_cannot_reanimate_expired_lease(self):
        enqueue(self.db, [{"id": "a", "scope": "room"}], 101)
        claim(self.db, 1, 110, 30)
        self.assertFalse(heartbeat(self.db, 1, "a", 141, attempt=1))
        self.assertTrue(heartbeat(self.db, 1, "a", 120, 30, attempt=1))

    def test_cross_worker_cannot_complete(self):
        enqueue(self.db, [{"id": "a", "scope": "room"}], 101)
        claim(self.db, 1, 110)
        self.assertFalse(finish(self.db, 2, "a", "review", 120, "PR#1", attempt=1))

    def test_concurrent_claims_unique(self):
        enqueue(self.db, [{"id": "job-%03d" % i, "scope": "scope-%03d" % i}
                          for i in range(300)], 101)

        def take(worker):
            connection = connect(self.path)
            try:
                return claim(connection, worker, 110)
            finally:
                connection.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as executor:
            jobs = list(executor.map(take, range(1, 301)))
        claimed = [j for j in jobs if j]
        self.assertEqual(len(claimed), 300)
        self.assertEqual(len({j["id"] for j in claimed}), 300)
        self.assertEqual(status(self.db)["states"]["running"], 300)

    def test_duplicate_id_conflicting_payload_fails_atomically(self):
        original = [{"id": "same", "scope": "room", "priority": 7,
                     "payload": {"issue": 1}}]
        self.assertEqual(enqueue(self.db, original, 101)["added"], 1)
        self.assertEqual(enqueue(self.db, original, 102)["existing"], 1)
        for changed in (
            {"id": "same", "scope": "other", "priority": 7, "payload": {"issue": 1}},
            {"id": "same", "scope": "room", "priority": 8, "payload": {"issue": 1}},
            {"id": "same", "scope": "room", "priority": 7, "payload": {"issue": 2}},
        ):
            with self.assertRaisesRegex(ValueError, "task id reused"):
                enqueue(self.db, [{"id": "new", "scope": "extra"}, changed], 103)
            self.assertIsNone(
                self.db.execute("SELECT 1 FROM tasks WHERE id='new'").fetchone()
            )
        task = claim(self.db, 1, 110)
        self.assertEqual(task["scope"], "room")
        self.assertEqual(task["attempts"], 1)

    def test_stale_attempt_cannot_mutate_reclaimed_task(self):
        enqueue(self.db, [{"id": "a", "scope": "room"}], 101)
        first = claim(self.db, 1, 110, 30)
        self.assertEqual(first["attempts"], 1)
        second = claim(self.db, 2, 141, 30)
        self.assertEqual(second["attempts"], 2)
        self.assertFalse(heartbeat(self.db, 1, "a", 142, attempt=1))
        self.assertFalse(finish(self.db, 1, "a", "review", 142,
                                "PR#old", attempt=1))
        # The same numeric worker slot can later be recycled (ABA case).
        third = claim(self.db, 1, 172, 30)
        self.assertEqual(third["attempts"], 3)
        self.assertFalse(heartbeat(self.db, 1, "a", 175, attempt=1))
        self.assertFalse(finish(self.db, 1, "a", "review", 175,
                                "PR#stale", attempt=1))
        self.assertTrue(heartbeat(self.db, 1, "a", 180, 30, attempt=3))
        self.assertTrue(finish(self.db, 1, "a", "review", 185,
                               "PR#current", attempt=3))
        self.assertEqual(status(self.db)["states"]["review"], 1)

    def test_retry_budget_blocks_fourth_manual_requeue(self):
        enqueue(self.db, [{"id": "a", "scope": "room"}], 101)
        for attempt in (1, 2):
            task = claim(self.db, attempt, 110 + attempt)
            self.assertEqual(task["attempts"], attempt)
            self.assertTrue(finish(self.db, attempt, "a", "queued",
                                   115 + attempt, attempt=attempt))
        third = claim(self.db, 3, 130)
        self.assertEqual(third["attempts"], 3)
        with self.assertRaisesRegex(ValueError, "retry budget exhausted"):
            finish(self.db, 3, "a", "queued", 131, attempt=3)
        self.assertTrue(finish(self.db, 3, "a", "blocked",
                               132, attempt=3))
        self.assertIsNone(claim(self.db, 4, 133))
        self.assertEqual(status(self.db)["states"]["blocked"], 1)

    def test_preexisting_exhausted_queue_is_quarantined(self):
        enqueue(self.db, [
            {"id": "exhausted", "scope": "room", "priority": 100},
            {"id": "eligible", "scope": "memory"},
        ], 101)
        self.db.execute(
            "UPDATE tasks SET attempts=? WHERE id='exhausted'",
            (MAX_ATTEMPTS,),
        )

        task = claim(self.db, 1, 110)

        self.assertEqual(task["id"], "eligible")
        exhausted = self.db.execute(
            "SELECT state, attempts FROM tasks WHERE id='exhausted'"
        ).fetchone()
        self.assertEqual(dict(exhausted), {
            "state": "blocked", "attempts": MAX_ATTEMPTS,
        })
        self.assertEqual(status(self.db)["states"]["blocked"], 1)

    def test_boolean_priority_rejected_instead_of_aliasing_integer(self):
        # Python's isinstance(True, int) is true and SQLite stores True as 1.
        # A second enqueue of the same ID with integer 1 must not silently
        # reinterpret a different priority type as an identical task.
        with self.assertRaisesRegex(ValueError, "priority must be an integer"):
            enqueue(self.db, [
                {"id": "safe", "scope": "other"},
                {"id": "bad", "scope": "room", "priority": True},
            ], 101)
        self.assertIsNone(self.db.execute(
            "SELECT 1 FROM tasks WHERE id='safe'"
        ).fetchone())
        self.assertEqual(enqueue(self.db, [
            {"id": "same", "scope": "room", "priority": 1},
        ], 102)["added"], 1)
        with self.assertRaisesRegex(ValueError, "priority must be an integer"):
            enqueue(self.db, [
                {"id": "same", "scope": "room", "priority": True},
            ], 103)
        self.assertEqual(self.db.execute(
            "SELECT priority FROM tasks WHERE id='same'"
        ).fetchone()[0], 1)

    def test_nonfinite_payload_is_not_persisted_as_nonstandard_json(self):
        # json.load accepts NaN/Infinity by default, but those are not JSON.
        # Preflight must reject them before committing any batch members.
        for number in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=repr(number)):
                with self.assertRaises(ValueError):
                    enqueue(self.db, [
                        {"id": "safe", "scope": "other"},
                        {"id": "bad", "scope": "room",
                         "payload": {"score": number}},
                    ], 101)
                self.assertIsNone(self.db.execute(
                    "SELECT 1 FROM tasks WHERE id='safe'"
                ).fetchone())

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            enqueue(self.db, [{"id": "x", "scope": "s"}, {"id": "x", "scope": "s"}], 111)


    def test_cli_closes_connection_on_success_and_failure(self):
        opened = []
        original_connect = coordinator.connect

        def tracked(path):
            connection = original_connect(path)
            opened.append(connection)
            return connection

        def invoke(*args):
            with patch.object(coordinator, "connect", side_effect=tracked):
                with patch.object(sys, "argv", [
                    "spartan_coordinator.py", "--db", self.path, *args,
                ]):
                    with redirect_stdout(StringIO()):
                        return coordinator.main()

        self.assertEqual(invoke("status"), 0)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[-1].execute("SELECT 1")

        with self.assertRaisesRegex(ValueError, "unknown agent id"):
            invoke("claim", "--worker", "999")
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[-1].execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
