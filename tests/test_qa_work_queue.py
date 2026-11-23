"""Concurrency, lease recovery, idempotency and restart tests for qa_work_queue."""

from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import unittest
from pathlib import Path

from tools.qa_work_queue import (
    DurableWorkQueue, IdempotencyConflict, LeaseLost, QueueError,
)


class FakeClock:
    def __init__(self, value=1000.0):
        self.value = value
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.value

    def advance(self, seconds):
        with self.lock:
            self.value += seconds


class DurableWorkQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()
        self.db = Path(self.tmp.name) / "queue.sqlite3"
        self.queue = DurableWorkQueue(self.db, clock=self.clock)

    def enqueue(self, task_id="task-1", key="idem-1", **kwargs):
        return self.queue.enqueue(task_id, key, {"pr": 42}, **kwargs)

    def test_enqueue_is_idempotent_and_conflicts_fail_closed(self):
        first = self.enqueue()
        second = self.enqueue()
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(self.queue.counts(), {"ready": 1})
        with self.assertRaises(IdempotencyConflict):
            self.queue.enqueue("other-task", "idem-1", {"pr": 99})
        with self.assertRaises(IdempotencyConflict):
            self.queue.enqueue("task-1", "other-key", {"pr": 42})

    def test_claim_is_atomic_under_competing_threads(self):
        for number in range(30):
            self.queue.enqueue(f"task-{number:02}", f"idem-{number:02}", {"n": number})
        barrier = threading.Barrier(12)

        def worker(index):
            barrier.wait()
            return self.queue.claim(f"worker-{index}", lease_seconds=30)

        with ThreadPoolExecutor(max_workers=12) as pool:
            claims = list(pool.map(worker, range(12)))
        claims = [claim for claim in claims if claim is not None]
        self.assertEqual(len(claims), 12)
        self.assertEqual(len({claim["task_id"] for claim in claims}), 12)
        self.assertEqual(len({claim["lease_token"] for claim in claims}), 12)

    def test_expired_lease_is_reclaimed_and_old_token_is_fenced(self):
        self.enqueue()
        first = self.queue.claim("worker-a", lease_seconds=5)
        self.clock.advance(6)
        second = self.queue.claim("worker-b", lease_seconds=5)
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        with self.assertRaises(LeaseLost):
            self.queue.complete(first["task_id"], first["lease_token"])
        self.assertEqual(self.queue.complete(second["task_id"], second["lease_token"])["status"], "done")

    def test_heartbeat_extends_only_live_lease(self):
        self.enqueue()
        claim = self.queue.claim("worker", lease_seconds=5)
        new_until = self.queue.heartbeat(claim["task_id"], claim["lease_token"], lease_seconds=20)
        self.assertEqual(new_until, self.clock() + 20)
        self.clock.advance(6)
        self.assertEqual(self.queue.get(claim["task_id"])["status"], "leased")
        self.queue.complete(claim["task_id"], claim["lease_token"])

    def test_expired_lease_cannot_be_resurrected_by_heartbeat(self):
        self.enqueue()
        claim = self.queue.claim("worker", lease_seconds=2)
        self.clock.advance(3)
        with self.assertRaises(LeaseLost):
            self.queue.heartbeat(claim["task_id"], claim["lease_token"])

    def test_retry_budget_and_terminal_dead_letter(self):
        self.enqueue(max_attempts=2)
        first = self.queue.claim("worker-a")
        retried = self.queue.retry(first["task_id"], first["lease_token"], error="provider offline")
        self.assertEqual(retried["status"], "ready")
        second = self.queue.claim("worker-b")
        dead = self.queue.retry(second["task_id"], second["lease_token"], error="still offline")
        self.assertEqual(dead["status"], "dead")
        self.assertIsNone(self.queue.claim("worker-c"))

    def test_due_time_defers_without_blocking_other_tasks(self):
        self.queue.enqueue("later", "later-key", {"n": 1}, available_at=2000)
        self.queue.enqueue("ready", "ready-key", {"n": 2})
        self.assertEqual(self.queue.claim("worker")["task_id"], "ready")

    def test_restart_preserves_ready_and_done_state(self):
        self.enqueue()
        claim = self.queue.claim("worker")
        self.queue.complete(claim["task_id"], claim["lease_token"])
        reopened = DurableWorkQueue(self.db, clock=self.clock)
        self.assertEqual(reopened.get("task-1")["status"], "done")
        self.assertEqual(reopened.counts(), {"done": 1})

    def test_crashed_worker_lease_survives_restart_and_is_reclaimed(self):
        self.enqueue()
        first = self.queue.claim("worker-that-crashes", lease_seconds=4)
        reopened = DurableWorkQueue(self.db, clock=self.clock)
        self.clock.advance(5)
        second = reopened.claim("recovery-worker", lease_seconds=4)
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertNotEqual(first["lease_token"], second["lease_token"])

    def test_repeated_crashes_exhaust_attempt_budget(self):
        self.enqueue(max_attempts=1)
        self.queue.claim("crashing-worker", lease_seconds=1)
        self.clock.advance(2)
        self.assertIsNone(self.queue.claim("recovery-worker"))
        self.assertEqual(self.queue.get("task-1")["status"], "dead")
        self.assertEqual(self.queue.counts(), {"dead": 1})

    def test_invalid_payloads_and_bounds_are_rejected(self):
        for payload in ({"nan": float("nan")}, {"bad": object()}):
            with self.subTest(payload=type(payload["bad" if "bad" in payload else "nan"]).__name__):
                with self.assertRaises(QueueError):
                    self.queue.enqueue("bad", "bad-key", payload)
        with self.assertRaises(QueueError):
            self.queue.claim("worker", lease_seconds=0)
        with self.assertRaises(QueueError):
            self.queue.enqueue("big", "big-key", "x" * (1024 * 1024 + 1))

    def test_stale_worker_cannot_retry_or_complete_after_reclaim(self):
        self.enqueue()
        first = self.queue.claim("worker-a", lease_seconds=1)
        self.clock.advance(2)
        second = self.queue.claim("worker-b", lease_seconds=10)
        with self.assertRaises(LeaseLost):
            self.queue.retry(first["task_id"], first["lease_token"])
        self.queue.complete(second["task_id"], second["lease_token"])


if __name__ == "__main__":
    unittest.main()
