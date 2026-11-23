"""Issue #701: deterministic offline delegation and fencing regressions."""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools import spartan_coordinator as sp
from tools import spartan_delegate as d


class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        sp.create_schema(self.db)
        sp.init(self.db, 2, now=10)
        sp.enqueue(self.db, [{
            "id": "issue-701", "scope": "spartan.executor",
            "payload": {"private_prompt": "DO_NOT_EXPORT"}
        }], now=11)
        self.assertEqual(sp.claim(self.db, 1, now=20, ttl=30)["attempts"], 1)
        d.install(self.db)
        self.lease = d.DelegatedLease("issue-701", 1, 1, "ray", "opaque.701")

    def tearDown(self):
        self.db.close()

    def test_wire_allowlist_and_no_private_payload(self):
        packet = d.encode_wire(self.lease)
        self.assertEqual(d.validate_wire(packet), self.lease)
        self.assertNotIn(b"DO_NOT_EXPORT", packet)
        self.assertEqual(set(json.loads(packet)), {
            "schema", "task_id", "worker_id", "attempt", "backend", "opaque_ref"})

    def test_wire_rejects_extra_fields_duplicates_and_boolean_attempt(self):
        for packet in (
            b"{}", b"[]" + b"x" * 1100,
            d.encode_wire(self.lease).replace(b'"attempt":1', b'"attempt":true'),
            d.encode_wire(self.lease).replace(b'"task_id":', b'"extra":1,"task_id":'),
            d.encode_wire(self.lease).replace(b'"task_id":', b'"task_id":"other","task_id":'),
        ):
            with self.subTest(packet=packet[:50]), self.assertRaises(d.DelegationRejected):
                d.validate_wire(packet)
        for task in ("../unsafe", "x\nlog", " ", "x" * 121):
            with self.assertRaises(d.DelegationRejected):
                d.DelegatedLease(task, 1, 1, "ray", "id")

    def test_first_dispatch_one_permit_ack_and_conflicts(self):
        self.assertTrue(d.reserve(self.db, self.lease, now=21))
        self.assertFalse(d.reserve(self.db, self.lease, now=22))
        changed = d.DelegatedLease("issue-701", 1, 1, "temporal", "opaque.701")
        with self.assertRaises(d.DelegationRejected):
            d.reserve(self.db, changed, now=23)
        self.assertTrue(d.acknowledge(self.db, self.lease, "remote.1", now=23))
        self.assertFalse(d.acknowledge(self.db, self.lease, "remote.1", now=23))
        with self.assertRaises(d.DelegationRejected):
            d.acknowledge(self.db, self.lease, "remote.2", now=23)

    def test_expiry_retry_and_orphan_reconciliation(self):
        self.assertTrue(d.reserve(self.db, self.lease, now=21))
        self.assertEqual(d.mark_orphans(self.db, now=51), 1)
        self.assertFalse(sp.heartbeat(self.db, 1, "issue-701", now=51, attempt=1))
        self.assertEqual(sp.claim(self.db, 2, now=51, ttl=30)["attempts"], 2)
        with self.assertRaises(d.DelegationRejected):
            d.acknowledge(self.db, self.lease, "late", now=52)
        with self.assertRaises(d.DelegationRejected):
            d.reserve(self.db, self.lease, now=52)
        retry = d.DelegatedLease("issue-701", 2, 2, "temporal", "opaque.702")
        self.assertTrue(d.reserve(self.db, retry, now=52))
        self.assertEqual(d.mark_orphans(self.db, now=52), 0)

    def test_cancel_intent_is_not_acknowledgement(self):
        d.reserve(self.db, self.lease, now=21)
        self.assertTrue(d.request_cancel(self.db, self.lease, now=22))
        self.assertFalse(d.request_cancel(self.db, self.lease, now=23))
        with self.assertRaises(d.DelegationRejected):
            d.acknowledge(self.db, self.lease, "late", now=24)
        with self.assertRaises(d.DelegationRejected):
            d.prepare_pr(self.db, self.lease, "feat/701", "a" * 40, now=24)

    def test_pr_ambiguous_publication_no_duplicate_permission(self):
        d.reserve(self.db, self.lease, now=21)
        d.acknowledge(self.db, self.lease, "remote.1", now=22)
        self.assertTrue(d.prepare_pr(self.db, self.lease, "feat/701", "a" * 40, now=23))
        self.assertFalse(d.prepare_pr(self.db, self.lease, "feat/701", "a" * 40, now=24))
        with self.assertRaises(d.DelegationRejected):
            d.prepare_pr(self.db, self.lease, "feat/other", "b" * 40, now=24)
        self.assertTrue(d.confirm_pr(
            self.db, "issue-701", 1, "a" * 40,
            "https://github.com/Garyu-tt/mojum/pull/999"))
        self.assertFalse(d.confirm_pr(
            self.db, "issue-701", 1, "a" * 40,
            "https://github.com/Garyu-tt/mojum/pull/999"))
        with self.assertRaises(d.DelegationRejected):
            d.confirm_pr(self.db, "issue-701", 1, "a" * 40, "https://wrong.invalid/")

    def test_retry_cannot_publish_second_pr_after_old_attempt(self):
        d.reserve(self.db, self.lease, now=21)
        d.acknowledge(self.db, self.lease, "remote.1", now=22)
        d.prepare_pr(self.db, self.lease, "feat/701", "a" * 40, now=23)
        sp.claim(self.db, 2, now=51, ttl=30)
        retry = d.DelegatedLease("issue-701", 2, 2, "temporal", "opaque.702")
        d.reserve(self.db, retry, now=52)
        d.acknowledge(self.db, retry, "remote.2", now=53)
        with self.assertRaises(d.DelegationRejected):
            d.prepare_pr(self.db, retry, "feat/retry", "b" * 40, now=54)
        # A previously published PR can still be reconciled after lease expiry.
        self.assertTrue(d.confirm_pr(
            self.db, "issue-701", 1, "a" * 40,
            "https://github.com/Garyu-tt/mojum/pull/999"))

    def test_fenced_expiry_rejects_mutation(self):
        d.reserve(self.db, self.lease, now=21)
        for action in (
            lambda: d.request_cancel(self.db, self.lease, now=50),
            lambda: d.acknowledge(self.db, self.lease, "late", now=50),
            lambda: d.prepare_pr(self.db, self.lease, "feat/701", "a" * 40, now=50)
        ):
            with self.assertRaises(d.DelegationRejected):
                action()
        self.assertEqual(self.db.execute("SELECT state FROM delegated_runs").fetchone()[0],
                         "reserved")

    def test_ray_adapter_with_injected_remote(self):
        class Remote:
            def __init__(self):
                self.calls = []
            def remote(self, packet):
                self.calls.append(packet)
                return "object-ref"
        remote = Remote()
        self.assertEqual(d.dispatch_ray(self.db, self.lease, remote, now=21), "object-ref")
        self.assertEqual(d.validate_wire(remote.calls[0]), self.lease)
        with self.assertRaises(d.DelegationRejected):
            d.dispatch_ray(self.db, self.lease, remote, now=22)
        self.assertEqual(len(remote.calls), 1)

    def test_temporal_adapter_with_injected_client(self):
        class Client:
            def __init__(self):
                self.calls = []
            async def start_workflow(self, name, arg, *, id, task_queue):
                self.calls.append((name, arg, id, task_queue))
                return "workflow-handle"
        client = Client()
        lease = d.DelegatedLease("issue-701", 1, 1, "temporal", "opaque.701")
        self.assertEqual(asyncio.run(d.dispatch_temporal(
            self.db, lease, client, task_queue="approved.queue", now=21)),
            "workflow-handle")
        self.assertEqual(client.calls[0][2], "mojum-issue-701-attempt-1")
        self.assertEqual(d.validate_wire(client.calls[0][1]), lease)
        with self.assertRaises(d.DelegationRejected):
            asyncio.run(d.dispatch_temporal(
                self.db, lease, client, task_queue="approved.queue", now=22))
        self.assertEqual(len(client.calls), 1)

    def test_ray_ambiguous_transport_exception_is_not_resubmitted(self):
        class FailingRemote:
            def __init__(self):
                self.calls = 0
            def remote(self, packet):
                self.calls += 1
                raise TimeoutError("simulated lost response")
        remote = FailingRemote()
        with self.assertRaises(TimeoutError):
            d.dispatch_ray(self.db, self.lease, remote, now=21)
        with self.assertRaises(d.DelegationRejected):
            d.dispatch_ray(self.db, self.lease, remote, now=22)
        self.assertEqual(remote.calls, 1)
        self.assertEqual(self.db.execute(
            "SELECT state FROM delegated_runs").fetchone()[0], "reserved")

    def test_temporal_ambiguous_transport_exception_is_not_resubmitted(self):
        class FailingClient:
            def __init__(self):
                self.calls = 0
            async def start_workflow(self, name, arg, *, id, task_queue):
                self.calls += 1
                raise TimeoutError("simulated lost response")
        client = FailingClient()
        lease = d.DelegatedLease("issue-701", 1, 1, "temporal", "opaque.701")
        with self.assertRaises(TimeoutError):
            asyncio.run(d.dispatch_temporal(
                self.db, lease, client, task_queue="approved.queue", now=21))
        with self.assertRaises(d.DelegationRejected):
            asyncio.run(d.dispatch_temporal(
                self.db, lease, client, task_queue="approved.queue", now=22))
        self.assertEqual(client.calls, 1)

    def test_durable_intent_after_sqlite_close_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.sqlite3")
            db = sqlite3.connect(path, isolation_level=None)
            db.row_factory = sqlite3.Row
            try:
                sp.create_schema(db)
                sp.init(db, 2, now=10)
                sp.enqueue(db, [{"id": "issue-701", "scope": "executor", "payload": {}}],
                           now=11)
                sp.claim(db, 1, now=20, ttl=30)
                d.install(db)
                d.reserve(db, self.lease, now=21)
                d.acknowledge(db, self.lease, "remote.1", now=22)
                self.assertTrue(d.prepare_pr(db, self.lease, "feat/701",
                                             "a" * 40, now=23))
            finally:
                db.close()
            db = sqlite3.connect(path, isolation_level=None)
            db.row_factory = sqlite3.Row
            try:
                self.assertFalse(d.prepare_pr(db, self.lease, "feat/701",
                                              "a" * 40, now=24))
            finally:
                db.close()

    def test_install_preserves_caller_transaction_and_rollback(self):
        # SQLite executescript() silently commits an active transaction.
        db = sqlite3.connect(":memory:", isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("CREATE TABLE tasks (id TEXT)")
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO tasks VALUES ('uncommitted')")
            d.install(db)
            self.assertTrue(db.in_transaction)
            db.execute("ROLLBACK")
            self.assertEqual(db.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)
            self.assertIsNone(db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='delegated_runs'"
            ).fetchone())
            self.assertIsNone(db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='delegated_pr_intents'"
            ).fetchone())
        finally:
            db.close()

    def test_delegation_refuses_ambient_transaction_without_side_effects(self):
        # Never dispatch after an uncommitted reservation that can be rolled back.
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("CREATE TABLE caller_marker (value TEXT)")
        self.db.execute("INSERT INTO caller_marker VALUES ('uncommitted')")
        for call in (
            lambda: d.reserve(self.db, self.lease, now=21),
            lambda: d.mark_orphans(self.db, now=21),
            lambda: d.confirm_pr(self.db, "issue-701", 1, "a" * 40,
                                 "https://github.com/Garyu-tt/mojum/pull/999"),
        ):
            with self.assertRaisesRegex(d.DelegationRejected,
                                        "finish caller transaction"):
                call()
            self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM delegated_runs").fetchone()[0], 0)
        self.db.execute("ROLLBACK")
        self.assertIsNone(self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='caller_marker'").fetchone())
        # Once the caller closes its own transaction, the admission is durable.
        self.assertTrue(d.reserve(self.db, self.lease, now=21))
        self.assertFalse(self.db.in_transaction)

    def test_install_needs_existing_lease_authority(self):
        empty = sqlite3.connect(":memory:", isolation_level=None)
        empty.row_factory = sqlite3.Row
        try:
            with self.assertRaises(d.DelegationRejected):
                d.install(empty)
        finally:
            empty.close()


if __name__ == "__main__":
    unittest.main()
