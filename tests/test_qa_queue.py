"""Tests for the durable QA queue; all cases are offline and stdlib-only."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from mojum.qa_queue import DurableQueue, InvalidJob, LeaseLost, MAX_PAYLOAD_BYTES


class FakeClock:
    def __init__(self, value: float = 1000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class DurableQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()
        self.path = Path(self.tmp.name) / "state" / "qa.sqlite3"
        self.queue = DurableQueue(self.path, clock=self.clock)

    def test_enqueue_is_idempotent_and_does_not_overwrite_original_payload(self):
        first = self.queue.enqueue("pr:12:head:abc", {"pr": 12, "head": "abc"})
        second = self.queue.enqueue("pr:12:head:abc", {"pr": 12, "head": "changed"})
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.payload, {"head": "abc", "pr": 12})
        self.assertEqual(second.status, "QUEUED")
        self.assertEqual(self.queue.counts()["QUEUED"], 1)

    def test_payload_is_bounded_and_strict_json(self):
        with self.assertRaises(InvalidJob):
            self.queue.enqueue("nan", {"value": float("nan")})
        with self.assertRaises(InvalidJob):
            self.queue.enqueue("large", {"data": "x" * MAX_PAYLOAD_BYTES})

    def test_claim_heartbeat_complete_and_ownership(self):
        queued = self.queue.enqueue("one", {"number": 1})
        claimed = self.queue.claim("worker-a", lease_seconds=10)
        self.assertEqual(claimed.id, queued.id)
        self.assertEqual(claimed.status, "CLAIMED")
        self.assertEqual(claimed.attempts, 1)
        with self.assertRaises(LeaseLost):
            self.queue.heartbeat(claimed.id, "worker-b", lease_seconds=10)
        self.clock.advance(5)
        extended = self.queue.heartbeat(claimed.id, "worker-a", lease_seconds=20)
        self.assertEqual(extended.lease_until, self.clock.value + 20)
        done = self.queue.complete(claimed.id, "worker-a")
        self.assertEqual(done.status, "COMPLETED")
        self.assertIsNone(done.lease_owner)
        self.assertIsNone(self.queue.claim("worker-b"))

    def test_expired_lease_is_reclaimed_after_restart(self):
        queued = self.queue.enqueue("restart", {"pr": 99})
        first = self.queue.claim("old-worker", lease_seconds=4)
        self.assertEqual(first.id, queued.id)
        self.clock.advance(4)
        restarted = DurableQueue(self.path, clock=self.clock)
        second = restarted.claim("new-worker", lease_seconds=10)
        self.assertEqual(second.id, queued.id)
        self.assertEqual(second.attempts, 2)
        with self.assertRaises(LeaseLost):
            restarted.complete(first.id, "old-worker")
        self.assertEqual(restarted.complete(second.id, "new-worker").status, "COMPLETED")

    def test_blocked_job_does_not_block_independent_ready_job(self):
        blocked = self.queue.enqueue("blocked", {"pr": 1})
        independent = self.queue.enqueue("independent", {"pr": 2})
        first = self.queue.claim("worker", lease_seconds=10)
        self.assertEqual(first.id, blocked.id)
        self.queue.block(first.id, "worker", "GitHub permission denied")
        next_job = self.queue.claim("worker-2", lease_seconds=10)
        self.assertEqual(next_job.id, independent.id)
        self.assertEqual(self.queue.counts()["BLOCKED"], 1)

    def test_blocked_job_requires_explicit_unblock(self):
        job = self.queue.enqueue("blocked", {"pr": 1})
        claim = self.queue.claim("worker", lease_seconds=10)
        self.queue.block(claim.id, "worker", "temporary failure")
        self.assertIsNone(self.queue.claim("worker-2"))
        unblocked = self.queue.unblock(job.id)
        self.assertEqual(unblocked.status, "QUEUED")
        self.assertIsNone(unblocked.last_error)
        self.assertEqual(self.queue.claim("worker-2").id, job.id)

    def test_concurrent_workers_never_receive_same_live_lease(self):
        for index in range(20):
            self.queue.enqueue(f"job-{index}", {"index": index})
        barrier = threading.Barrier(8)
        claimed_ids: list[int] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker(index: int) -> None:
            try:
                barrier.wait()
                job = self.queue.claim(f"worker-{index}", lease_seconds=30)
                if job is not None:
                    with lock:
                        claimed_ids.append(job.id)
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(claimed_ids), len(set(claimed_ids)))
        self.assertEqual(len(claimed_ids), 8)

    def test_unknown_job_and_invalid_arguments_fail_closed(self):
        self.assertIsNone(self.queue.get(999))
        with self.assertRaises(InvalidJob):
            self.queue.enqueue(" ", {})
        with self.assertRaises(InvalidJob):
            self.queue.claim("worker", lease_seconds=0)
        with self.assertRaises(LeaseLost):
            self.queue.complete(999, "worker")


if __name__ == "__main__":
    unittest.main()
