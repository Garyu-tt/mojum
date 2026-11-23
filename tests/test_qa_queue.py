"""Adversarial and concurrency tests for the durable QA work queue."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import tempfile
from contextlib import closing
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import warnings
import gc

from mojum.qa_queue import (
    DurableTaskQueue, DuplicateTaskError, LeaseLostError, QueueError,
)


class DurableTaskQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "queue.sqlite3"
        self.queue = DurableTaskQueue(self.path)

    def test_enqueue_is_idempotent_and_payload_is_detached(self) -> None:
        payload = {"pr": 42, "labels": ["qa", "cpu"]}
        first = self.queue.enqueue("pr:42", "event:abc", payload, now_ns=10)
        payload["labels"].append("mutated")
        again = self.queue.enqueue(
            "pr:42", "event:abc", {"labels": ["qa", "cpu"], "pr": 42}, now_ns=20
        )
        self.assertEqual(first.task_id, again.task_id)
        self.assertEqual(again.payload["labels"], ["qa", "cpu"])
        self.assertEqual(len(self.queue.events(first.task_id)), 1)

    def test_idempotency_key_cannot_alias_another_payload(self) -> None:
        self.queue.enqueue("pr:1", "same-key", {"head": "a"}, now_ns=1)
        with self.assertRaises(DuplicateTaskError):
            self.queue.enqueue("pr:1", "same-key", {"head": "b"}, now_ns=2)
        with self.assertRaises(DuplicateTaskError):
            self.queue.enqueue("pr:2", "same-key", {"head": "a"}, now_ns=2)

    def test_delayed_task_is_not_claimed_early(self) -> None:
        self.queue.enqueue("later", "later-1", {}, available_at_ns=50, now_ns=10)
        self.assertIsNone(self.queue.claim("worker", now_ns=49))
        self.assertEqual(self.queue.claim("worker", now_ns=50).task_key, "later")

    def test_expired_lease_is_recovered_and_old_owner_is_fenced(self) -> None:
        queued = self.queue.enqueue("pr:1", "one", {}, now_ns=1_000_000_000)
        first = self.queue.claim("worker-a", lease_seconds=2, now_ns=1_000_000_000)
        self.assertEqual(first.task_id, queued.task_id)
        second = self.queue.claim("worker-b", lease_seconds=2, now_ns=3_000_000_000)
        self.assertEqual(second.task_id, queued.task_id)
        self.assertNotEqual(first.lease_token, second.lease_token)
        self.assertEqual(second.attempts, 2)
        with self.assertRaises(LeaseLostError):
            self.queue.complete(first.task_id, "worker-a", first.lease_token, now_ns=3_000_000_001)
        done = self.queue.complete(second.task_id, "worker-b", second.lease_token, now_ns=3_000_000_001)
        self.assertEqual(done.state, "completed")
        self.assertIsNone(self.queue.claim("worker-c", now_ns=4_000_000_000))

    def test_heartbeat_extends_lease_and_rejects_wrong_worker(self) -> None:
        task = self.queue.enqueue("pr:2", "two", {}, now_ns=1)
        leased = self.queue.claim("worker-a", lease_seconds=2, now_ns=1)
        with self.assertRaises(LeaseLostError):
            self.queue.heartbeat(task.task_id, "worker-b", leased.lease_token, now_ns=2)
        renewed = self.queue.heartbeat(task.task_id, "worker-a", leased.lease_token,
                                       lease_seconds=5, now_ns=2)
        self.assertEqual(renewed.lease_until_ns, 5_000_000_002)
        self.assertIsNone(self.queue.claim("worker-b", now_ns=2_000_000_003))

    def test_failure_can_be_terminal_or_requeued(self) -> None:
        self.queue.enqueue("retry", "retry-1", {}, now_ns=1)
        first = self.queue.claim("worker", now_ns=1)
        queued = self.queue.fail(first.task_id, "worker", first.lease_token,
                                 retry=True, available_at_ns=100, now_ns=2)
        self.assertEqual(queued.state, "queued")
        self.assertIsNone(self.queue.claim("worker", now_ns=99))
        second = self.queue.claim("worker", now_ns=100)
        failed = self.queue.fail(second.task_id, "worker", second.lease_token, now_ns=101)
        self.assertEqual(failed.state, "failed")
        self.assertIsNone(self.queue.claim("worker", now_ns=200))

    def test_restart_preserves_tasks_and_audit(self) -> None:
        task = self.queue.enqueue("persist", "persist-1", {"safe": True}, now_ns=1)
        claimed = self.queue.claim("worker", now_ns=1)
        reopened = DurableTaskQueue(self.path)
        restored = reopened.get(task.task_id)
        self.assertEqual(restored.payload, {"safe": True})
        self.assertIsNone(restored.lease_token)
        self.assertIsNotNone(claimed.lease_token)
        self.assertEqual([e.event for e in reopened.events(task.task_id)],
                         ["enqueued", "claimed"])

    def test_parallel_workers_never_receive_same_live_lease(self) -> None:
        for number in range(12):
            self.queue.enqueue(f"task:{number}", f"key:{number}", {"n": number}, now_ns=1)

        def take(index: int):
            return DurableTaskQueue(self.path).claim(f"worker:{index}", now_ns=1)

        with ThreadPoolExecutor(max_workers=12) as pool:
            claimed = list(pool.map(take, range(12)))
        ids = [task.task_id for task in claimed if task is not None]
        self.assertEqual(len(ids), 12)
        self.assertEqual(len(set(ids)), 12)

    def test_invalid_and_oversized_inputs_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            self.queue.enqueue(" ", "key", {}, now_ns=1)
        with self.assertRaises(ValueError):
            self.queue.enqueue("ok", "key", {"bad": float("nan")}, now_ns=1)
        with self.assertRaises(ValueError):
            self.queue.enqueue("ok", "key2", {"x": "x" * 70_000}, now_ns=1)
        with self.assertRaises(ValueError):
            self.queue.enqueue("ok", "key3", {1: "not-a-string-key"}, now_ns=1)
        with self.assertRaises(ValueError):
            self.queue.enqueue("ok", "key4", {"tuple": (1, 2)}, now_ns=1)
        with self.assertRaises(ValueError):
            self.queue.enqueue("ok", "key5", {"surrogate": "\ud800"}, now_ns=1)
        with self.assertRaises(ValueError):
            self.queue.claim("worker", lease_seconds=True, now_ns=1)
        with self.assertRaises(ValueError):
            self.queue.claim("worker", now_ns=True)
        with self.assertRaises(ValueError):
            DurableTaskQueue(":memory:")

    def test_get_redacts_active_lease_token(self) -> None:
        self.queue.enqueue("private", "private-1", {}, now_ns=1)
        claimed = self.queue.claim("worker-a", now_ns=1)
        observed = self.queue.get(claimed.task_id)
        self.assertIsNone(observed.lease_token)
        self.assertIsNotNone(claimed.lease_token)

    def test_payload_node_and_aggregate_text_budgets_precede_serialization(self) -> None:
        with patch("mojum.qa_queue.json.dumps", side_effect=AssertionError("serialized before admission")):
            with self.assertRaisesRegex(ValueError, "node count"):
                self.queue.enqueue("many", "many-1", {"items": [None] * 10_001}, now_ns=1)
            with self.assertRaisesRegex(ValueError, "aggregate text"):
                self.queue.enqueue("text", "text-1", {"a": "x" * 40_000, "b": "y" * 40_000}, now_ns=1)

    def test_unversioned_and_unknown_schema_are_refused(self) -> None:
        legacy = Path(self.temp.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy)) as db:
            db.execute("CREATE TABLE queue_tasks (task_id INTEGER)")
        with self.assertRaisesRegex(QueueError, "unversioned queue database"):
            DurableTaskQueue(legacy)

        unknown = Path(self.temp.name) / "unknown.sqlite3"
        with closing(sqlite3.connect(unknown)) as db:
            db.execute("CREATE TABLE queue_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO queue_meta VALUES('schema_version', '99')")
        with self.assertRaisesRegex(QueueError, "unsupported or missing queue schema version"):
            DurableTaskQueue(unknown)

    def test_non_queue_database_is_not_adopted(self) -> None:
        unrelated = Path(self.temp.name) / "application.sqlite3"
        with closing(sqlite3.connect(unrelated)) as db:
            db.execute("CREATE TABLE application_data (value TEXT NOT NULL)")
            db.execute("INSERT INTO application_data VALUES ('preserve me')")
        with self.assertRaisesRegex(QueueError, "database is not empty"):
            DurableTaskQueue(unrelated)
        with closing(sqlite3.connect(unrelated)) as db:
            self.assertEqual(
                db.execute("SELECT value FROM application_data").fetchone()[0],
                "preserve me",
            )
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='queue_meta'"
                ).fetchone()
            )

    def test_operational_connections_are_closed_on_success_and_error(self) -> None:
        original = self.queue._connect
        opened = []

        def capture():
            connection = original()
            opened.append(connection)
            return connection

        self.queue._connect = capture
        self.queue.get(1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[-1].execute("SELECT 1")

        with self.assertRaises(LeaseLostError):
            self.queue.heartbeat(1, "wrong", "invalid", now_ns=2)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[-1].execute("SELECT 1")

    def test_repeated_operations_emit_no_unclosed_connection_warning(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            for index in range(20):
                self.queue.enqueue(f"resource:{index}", f"resource:{index}", {}, now_ns=index + 1)
                self.queue.get(index + 1)
            gc.collect()

    def test_event_limit_is_bounded(self) -> None:
        task = self.queue.enqueue("events", "events-1", {}, now_ns=1)
        claimed = self.queue.claim("worker", now_ns=1)
        self.queue.complete(task.task_id, "worker", claimed.lease_token, now_ns=2)
        self.assertEqual(len(self.queue.events(task.task_id, limit=2)), 2)
        with self.assertRaises(ValueError):
            self.queue.events(task.task_id, limit=1_001)


if __name__ == "__main__":
    unittest.main()
