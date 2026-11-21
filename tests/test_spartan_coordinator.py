"""Atomic dispatcher tests: no LLM required."""
import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.spartan_coordinator import (
    claim, connect, create_schema, enqueue, finish, heartbeat, init, status,
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
            finish(self.db, 1, "a", "review", 115)
        with self.assertRaises(ValueError):
            finish(self.db, 1, "a", "done", 115, "PR#1")
        self.assertTrue(finish(self.db, 1, "a", "review", 115, "PR#1"))
        self.assertFalse(finish(self.db, 1, "a", "blocked", 116))
        self.assertEqual(status(self.db)["states"]["review"], 1)

    def test_heartbeat_cannot_reanimate_expired_lease(self):
        enqueue(self.db, [{"id": "a", "scope": "room"}], 101)
        claim(self.db, 1, 110, 30)
        self.assertFalse(heartbeat(self.db, 1, "a", 141))
        self.assertTrue(heartbeat(self.db, 1, "a", 120, 30))

    def test_cross_worker_cannot_complete(self):
        enqueue(self.db, [{"id": "a", "scope": "room"}], 101)
        claim(self.db, 1, 110)
        self.assertFalse(finish(self.db, 2, "a", "review", 120, "PR#1"))

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

    def test_worker_requeue_consumes_attempts_and_blocks_at_limit(self):
        enqueue(self.db, [{"id": "a", "scope": "room"}], 101)
        for attempt in range(1, 4):
            now = 110 + 2 * attempt
            task = claim(self.db, 1, now, 30)
            self.assertEqual(task["id"], "a")
            self.assertEqual(task["attempts"], attempt)
            self.assertTrue(finish(self.db, 1, "a", "queued", now + 1))
            expected = "blocked" if attempt == 3 else "queued"
            self.assertEqual(status(self.db)["states"][expected], 1)
        self.assertIsNone(claim(self.db, 2, 150, 30))
        row = self.db.execute("SELECT state, attempts FROM tasks WHERE id='a'").fetchone()
        self.assertEqual(tuple(row), ("blocked", 3))

    def test_legacy_exhausted_queued_record_is_quarantined(self):
        enqueue(self.db, [{"id": "a", "scope": "room"}], 101)
        # An earlier coordinator revision allowed this persisted state.
        self.db.execute("UPDATE tasks SET attempts=3 WHERE id='a'")
        self.assertIsNone(claim(self.db, 1, 110, 30))
        row = self.db.execute("SELECT state, attempts FROM tasks WHERE id='a'").fetchone()
        self.assertEqual(tuple(row), ("blocked", 3))

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            enqueue(self.db, [{"id": "x", "scope": "s"}, {"id": "x", "scope": "s"}], 111)


if __name__ == "__main__":
    unittest.main()
