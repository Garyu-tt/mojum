from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from mojum.qa_work_queue import QueueError, claim, enqueue, finish, heartbeat, release


SHA = "a" * 40


class WorkQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "queue.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def add(self, key="pr-7-review", created_at=10):
        return enqueue(self.db, idempotency_key=key, kind="review", pr_number=7,
                       head_sha=SHA, created_at=created_at, payload={"source": "github"})

    def test_enqueue_is_idempotent_and_preserves_original(self):
        first = self.add()
        again = enqueue(self.db, idempotency_key="pr-7-review", kind="different",
                        pr_number=8, head_sha="b" * 40, created_at=99)
        self.assertEqual(first, again)
        self.assertEqual(first.status, "queued")

    def test_claim_heartbeat_finish(self):
        self.add()
        task = claim(self.db, owner="worker-a", now=100, lease_ms=50)
        self.assertEqual(task.status, "leased")
        self.assertEqual(task.attempts, 1)
        renewed = heartbeat(self.db, task_id=task.task_id, owner="worker-a",
                            lease_token=task.lease_token, now=120, lease_ms=100)
        self.assertEqual(renewed.lease_expires_at, 220)
        done = finish(self.db, task_id=task.task_id, owner="worker-a",
                      lease_token=task.lease_token, now=130, success=True)
        self.assertEqual(done.status, "done")
        self.assertIsNone(claim(self.db, owner="worker-b", now=140, lease_ms=20))

    def test_expired_lease_reclaimed_and_old_token_rejected(self):
        self.add()
        first = claim(self.db, owner="worker-a", now=100, lease_ms=10)
        second = claim(self.db, owner="worker-b", now=110, lease_ms=10)
        self.assertEqual(second.task_id, first.task_id)
        self.assertEqual(second.attempts, 2)
        with self.assertRaises(QueueError):
            finish(self.db, task_id=first.task_id, owner="worker-a",
                   lease_token=first.lease_token, now=111, success=True)
        self.assertEqual(finish(self.db, task_id=second.task_id, owner="worker-b",
                                lease_token=second.lease_token, now=111,
                                success=False).status, "failed")

    def test_release_makes_task_available(self):
        self.add()
        first = claim(self.db, owner="worker-a", now=1, lease_ms=5)
        released = release(self.db, task_id=first.task_id, owner="worker-a",
                           lease_token=first.lease_token, now=2)
        self.assertEqual(released.status, "queued")
        second = claim(self.db, owner="worker-b", now=3, lease_ms=5)
        self.assertEqual(second.task_id, first.task_id)
        self.assertEqual(second.attempts, 2)

    def test_two_connections_have_one_claim_winner(self):
        self.add()
        barrier = threading.Barrier(2)
        results = []
        errors = []
        def worker(name):
            try:
                barrier.wait()
                results.append(claim(self.db, owner=name, now=100, lease_ms=50))
            except Exception as exc:  # pragma: no cover - assertion captures diagnostics
                errors.append(exc)
        threads = [threading.Thread(target=worker, args=(name,)) for name in ("worker-a", "worker-b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(errors, errors)
        self.assertEqual(sum(item is not None for item in results), 1)

    def test_reopen_preserves_queue_and_reclaims_after_expiry(self):
        self.add()
        first = claim(self.db, owner="worker-a", now=10, lease_ms=5)
        # Every operation closes its connection; this is a close/reopen recovery check.
        second = claim(self.db, owner="worker-b", now=15, lease_ms=5)
        self.assertEqual(second.task_id, first.task_id)

    def test_rejects_bool_and_malformed_inputs(self):
        with self.assertRaises(QueueError):
            enqueue(self.db, idempotency_key="x", kind="review", pr_number=True,
                    head_sha=SHA, created_at=0)
        with self.assertRaises(QueueError):
            enqueue(self.db, idempotency_key="x", kind="review", pr_number=1,
                    head_sha="not-a-sha", created_at=0)
        with self.assertRaises(QueueError):
            enqueue(self.db, idempotency_key="x", kind="review", pr_number=1,
                    head_sha=SHA, created_at=0, payload={"private": "must not pass"})
        with self.assertRaises(QueueError):
            claim(self.db, owner="worker", now=0, lease_ms=True)

    def test_expired_lease_cannot_be_heartbeated(self):
        self.add()
        task = claim(self.db, owner="worker-a", now=10, lease_ms=5)
        with self.assertRaises(QueueError):
            heartbeat(self.db, task_id=task.task_id, owner="worker-a",
                      lease_token=task.lease_token, now=15, lease_ms=5)


if __name__ == "__main__":
    unittest.main()
