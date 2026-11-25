from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_heartbeat_never_shortens_a_live_lease(self):
        self.add()
        first = claim(self.db, owner="worker-a", now=1, lease_ms=100)
        renewed = heartbeat(self.db, task_id=first.task_id, owner="worker-a",
                            lease_token=first.lease_token, now=2, lease_ms=1)
        self.assertEqual(renewed, first)
        self.assertIsNone(claim(self.db, owner="worker-b", now=3, lease_ms=10))
        extended = heartbeat(self.db, task_id=first.task_id, owner="worker-a",
                             lease_token=first.lease_token, now=50, lease_ms=100)
        self.assertEqual(extended.lease_expires_at, 150)
        self.assertEqual(extended.lease_token, first.lease_token)
        self.assertEqual(extended.lease_owner, first.lease_owner)
        self.assertEqual(extended.attempts, first.attempts)

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

    def test_queued_work_precedes_expired_lease_reclaim(self):
        first = self.add(key="blocked-old", created_at=1)
        first_lease = claim(self.db, owner="worker-a", now=1, lease_ms=1)
        self.assertEqual(first_lease.task_id, first.task_id)

        queued = self.add(key="independent-new", created_at=2)
        independent = claim(self.db, owner="worker-b", now=2, lease_ms=10)
        self.assertEqual(independent.task_id, queued.task_id)
        self.assertEqual(independent.status, "leased")

        reclaimed = claim(self.db, owner="worker-c", now=2, lease_ms=10)
        self.assertEqual(reclaimed.task_id, first.task_id)
        self.assertEqual(reclaimed.attempts, 2)

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

    def test_invalid_integer_arguments_are_rejected_before_database_creation(self):
        ownership = dict(task_id=1, owner="worker", lease_token="a" * 64, now=0)
        cases = [
            (enqueue, dict(idempotency_key="x", kind="review", pr_number=1,
                           head_sha=SHA, created_at=0), ("pr_number", "created_at")),
            (claim, dict(owner="worker", now=0, lease_ms=1), ("now", "lease_ms")),
            (heartbeat, dict(ownership, lease_ms=1), ("task_id", "now", "lease_ms")),
            (finish, dict(ownership, success=True), ("task_id", "now")),
            (release, ownership, ("task_id", "now")),
        ]
        for operation, valid, fields in cases:
            for field in fields:
                for invalid in (2**63, True, -1):
                    with self.subTest(operation=operation.__name__, field=field, value=invalid):
                        arguments = dict(valid, **{field: invalid})
                        with patch("mojum.qa_work_queue._connect") as connect:
                            with self.assertRaises(QueueError):
                                operation(self.db, **arguments)
                            connect.assert_not_called()
                        self.assertFalse(Path(self.db).exists())

    def test_invalid_ownership_is_rejected_before_database_creation(self):
        ownership = dict(task_id=1, owner="worker", lease_token="a" * 64, now=0)
        for operation, extra in ((heartbeat, {"lease_ms": 1}),
                                 (finish, {"success": True}), (release, {})):
            for field, invalid in (("owner", ""), ("lease_token", "invalid")):
                with self.subTest(operation=operation.__name__, field=field):
                    arguments = dict(ownership, **extra)
                    arguments[field] = invalid
                    with patch("mojum.qa_work_queue._connect") as connect:
                        with self.assertRaises(QueueError):
                            operation(self.db, **arguments)
                        connect.assert_not_called()
                    self.assertFalse(Path(self.db).exists())

    def test_largest_signed_integer_metadata_is_accepted(self):
        maximum = 2**63 - 1
        task = enqueue(self.db, idempotency_key="boundary", kind="review",
                       pr_number=maximum, head_sha=SHA, created_at=maximum)
        self.assertEqual(task.pr_number, maximum)
        self.assertEqual(task.created_at, maximum)

    def test_largest_lease_expiry_is_accepted(self):
        maximum = 2**63 - 1
        self.add()
        task = claim(self.db, owner="worker", now=maximum - 1, lease_ms=1)
        self.assertEqual(task.lease_expires_at, maximum)
        renewed = heartbeat(self.db, task_id=task.task_id, owner="worker",
                            lease_token=task.lease_token, now=maximum - 1, lease_ms=1)
        self.assertEqual(renewed, task)
        done = finish(self.db, task_id=task.task_id, owner="worker",
                      lease_token=task.lease_token, now=maximum - 1, success=True)
        self.assertEqual(done.status, "done")

    def test_lease_expiry_overflow_is_rejected_before_database_creation(self):
        for operation, extra in ((claim, {}),
                                 (heartbeat, {"task_id": 1, "lease_token": "a" * 64})):
            for now, duration in ((2**63 - 1, 1), (1, 2**63 - 1)):
                with self.subTest(operation=operation.__name__, now=now, duration=duration):
                    with patch("mojum.qa_work_queue._connect") as connect:
                        with self.assertRaises(QueueError):
                            operation(self.db, owner="worker", now=now,
                                      lease_ms=duration, **extra)
                        connect.assert_not_called()
                    self.assertFalse(Path(self.db).exists())

    def test_failed_connection_setup_closes_database(self):
        class FailingPragmaConnection(sqlite3.Connection):
            failed_pragma = None

            def execute(self, sql, parameters=()):
                if sql == self.failed_pragma:
                    raise sqlite3.OperationalError("injected connection setup failure")
                return super().execute(sql, parameters)

        for pragma in ("PRAGMA busy_timeout=2000", "PRAGMA journal_mode=WAL",
                       "PRAGMA synchronous=FULL", "PRAGMA foreign_keys=ON"):
            with self.subTest(pragma=pragma):
                db = sqlite3.connect(self.db, isolation_level=None,
                                     factory=FailingPragmaConnection)
                db.failed_pragma = pragma
                try:
                    with patch("mojum.qa_work_queue.sqlite3.connect", return_value=db):
                        with self.assertRaisesRegex(sqlite3.OperationalError, "setup failure"):
                            claim(self.db, owner="worker", now=1, lease_ms=20)
                    with self.assertRaises(sqlite3.ProgrammingError):
                        db.execute("SELECT 1")
                finally:
                    db.close()

    def test_payload_rejects_hostile_string_subclass_keys_before_comparison(self):
        class HostileKey(str):
            __hash__ = str.__hash__
            def __eq__(self, other):
                raise AssertionError("untrusted key equality must not execute")

        with self.assertRaises(QueueError):
            enqueue(self.db, idempotency_key="hostile-key", kind="review", pr_number=1,
                    head_sha=SHA, created_at=0, payload={HostileKey("source"): "github"})

    def test_payload_rejects_oversized_key_count_before_hashing(self):
        payload = {"source": "a", "reason": "b", "workflow": "c", "extra": "d"}
        with self.assertRaises(QueueError):
            enqueue(self.db, idempotency_key="oversized", kind="review", pr_number=1,
                    head_sha=SHA, created_at=0, payload=payload)

    def test_expired_lease_cannot_be_heartbeated(self):
        self.add()
        task = claim(self.db, owner="worker-a", now=10, lease_ms=5)
        with self.assertRaises(QueueError):
            heartbeat(self.db, task_id=task.task_id, owner="worker-a",
                      lease_token=task.lease_token, now=15, lease_ms=5)


if __name__ == "__main__":
    unittest.main()
