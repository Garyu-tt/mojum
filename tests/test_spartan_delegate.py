"""Offline, deterministic crash/retry and idempotency tests for #701."""
from contextlib import closing
from pathlib import Path
import concurrent.futures
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import spartan_coordinator as sc
from tools import spartan_delegate as sd

SHA = "a" * 64


class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "coordinator.sqlite3")
        self.db = sc.connect(self.path)
        self.addCleanup(self.db.close)
        sc.create_schema(self.db)
        sc.init(self.db, 4, 100)
        sd.create_schema(self.db)
        sc.enqueue(self.db, [{"id": "task-a", "scope": "scope-a",
                              "payload": {"prompt": "SECRET DO NOT EXPORT"}}], 100)
        self.first = sc.claim(self.db, 1, 110, 30)

    def ticket(self, now=111):
        return sd.submit(self.db, "task-a", 1, 1, now)

    def test_idempotent_submit_and_sanitized_driver_packet(self):
        ticket = self.ticket()
        self.assertEqual(self.ticket(), ticket)
        for backend in ("ray", "temporal", "fixture"):
            packet = ticket.routing_packet(backend)
            self.assertEqual(packet["schema"], sd.SCHEMA)
            self.assertEqual(len(packet["dispatch_id"]), 64)
            self.assertNotIn("SECRET", str(packet))
            self.assertNotIn("task-a", str(packet))
            self.assertNotIn("scope-a", str(packet))
        with self.assertRaises(sd.DelegationError):
            ticket.routing_packet("http://external")
        self.assertEqual(sd.states(self.db)["pending"], 1)

    def test_manual_ticket_cannot_export_unvalidated_dispatch_or_deadline(self):
        valid = self.ticket()
        forged = (
            sd.Ticket(valid.task_id, valid.worker_id, valid.attempt,
                      "SECRET PRIVATE TOKEN", valid.lease_until),
            sd.Ticket(valid.task_id, valid.worker_id, valid.attempt,
                      "f" * 64, valid.lease_until),
            sd.Ticket(valid.task_id, 301, valid.attempt,
                      valid.dispatch_id, valid.lease_until),
            sd.Ticket(valid.task_id, valid.worker_id, 4,
                      valid.dispatch_id, valid.lease_until),
            sd.Ticket(valid.task_id, valid.worker_id, valid.attempt,
                      valid.dispatch_id, -1),
        )
        for ticket in forged:
            with self.subTest(ticket=ticket), self.assertRaises(sd.DelegationError):
                ticket.routing_packet("ray")
        class SpoofBackend(int):
            def __eq__(self, other):
                return other == "ray"
            def __ne__(self, other):
                return not self.__eq__(other)
        with self.assertRaises(sd.DelegationError):
            valid.routing_packet(SpoofBackend(7))
        self.assertEqual(valid.routing_packet("fixture")["dispatch_id"], valid.dispatch_id)

    def test_start_report_and_single_pr_intent(self):
        ticket = self.ticket()
        self.assertTrue(sd.start(self.db, ticket, 112))
        self.assertFalse(sd.start(self.db, ticket, 113))
        self.assertTrue(sd.report(self.db, ticket, 114, SHA))
        self.assertFalse(sd.report(self.db, ticket, 115, SHA))
        self.assertTrue(sd.prepare_pr_intent(self.db, ticket, 116, SHA))
        self.assertFalse(sd.prepare_pr_intent(self.db, ticket, 117, SHA))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM delegated_pr_intents").fetchone()[0], 1)
        with self.assertRaises(sd.DelegationError):
            sd.report(self.db, ticket, 118, "Z" * 64)
        with self.assertRaises(sd.DelegationError):
            sd.cancel(self.db, ticket, 119)

    def test_cancel_prevents_results_and_revive(self):
        ticket = self.ticket()
        self.assertTrue(sd.cancel(self.db, ticket, 112))
        self.assertFalse(sd.cancel(self.db, ticket, 113))
        with self.assertRaises(sd.DelegationError):
            sd.start(self.db, ticket, 114)
        with self.assertRaises(sd.DelegationError):
            sd.report(self.db, ticket, 115, SHA)
        with self.assertRaises(sd.DelegationError):
            self.ticket(116)

    def test_host_failure_retry_fences_old_attempt(self):
        old = self.ticket()
        sd.start(self.db, old, 112)
        # Worker 2 replaces lease at time 141; same old worker can be recycled.
        second = sc.claim(self.db, 2, 141, 30)
        self.assertEqual(second["attempts"], 2)
        new = sd.submit(self.db, "task-a", 2, 2, 142)
        self.assertEqual(sd.states(self.db)["orphaned"], 1)
        with self.assertRaises(sd.DelegationError):
            sd.report(self.db, old, 143, SHA)
        with self.assertRaises(sd.DelegationError):
            sd.prepare_pr_intent(self.db, old, 143, SHA)
        sd.start(self.db, new, 144)
        sd.report(self.db, new, 145, SHA)
        self.assertTrue(sd.prepare_pr_intent(self.db, new, 146, SHA))
        self.assertFalse(sd.prepare_pr_intent(self.db, new, 147, SHA))

    def test_reconcile_stale_orphan_without_mutating_canonical_tasks(self):
        old = self.ticket()
        self.assertEqual(sd.reconcile(self.db, 120), 0)
        self.assertEqual(sd.reconcile(self.db, 141), 1)
        self.assertEqual(sd.reconcile(self.db, 142), 0)
        with self.assertRaises(sd.DelegationError):
            sd.start(self.db, old, 142)
        row = self.db.execute("SELECT state, attempts, worker_id FROM tasks WHERE id='task-a'").fetchone()
        self.assertEqual(tuple(row), ("running", 1, 1))

    def test_expired_lease_denies_every_mutation(self):
        ticket = self.ticket()
        for callback in (lambda: sd.start(self.db, ticket, 140),
                         lambda: sd.cancel(self.db, ticket, 140),
                         lambda: sd.report(self.db, ticket, 140, SHA),
                         lambda: sd.submit(self.db, "task-a", 1, 1, 140)):
            with self.assertRaises(sd.DelegationError):
                callback()

    def test_rejects_nonfinite_bool_and_forged_ticket(self):
        for worker, attempt, now in ((True, 1, 112), (1, False, 112),
                                     (1, 1, True), (0, 1, 112), (1, 4, 112),
                                     (2, 1, 112)):
            with self.subTest(worker=worker, attempt=attempt, now=now):
                with self.assertRaises(sd.DelegationError):
                    sd.submit(self.db, "task-a", worker, attempt, now)
        t = self.ticket()
        forged = sd.Ticket(t.task_id, t.worker_id, t.attempt, "0" * 64, t.lease_until)
        with self.assertRaises(sd.DelegationError):
            sd.start(self.db, forged, 112)
        with self.assertRaises(sd.DelegationError):
            sd.report(self.db, t, 113, "f" * 63)

    def test_pr_intent_cannot_be_reissued_on_later_attempt(self):
        t = self.ticket()
        sd.start(self.db, t, 112)
        sd.report(self.db, t, 113, SHA)
        self.assertTrue(sd.prepare_pr_intent(self.db, t, 114, SHA))
        # The old attempt has a durable intent. The coordinator may recover
        # only after expiry, never blindly authorize a second publication.
        second = sc.claim(self.db, 2, 141, 30)
        self.assertEqual(second["attempts"], 2)
        nt = sd.submit(self.db, "task-a", 2, 2, 142)
        sd.start(self.db, nt, 143)
        sd.report(self.db, nt, 144, "b" * 64)
        self.assertFalse(sd.prepare_pr_intent(self.db, nt, 145, "b" * 64))

    def test_parallel_same_attempt_is_unique_and_durable_after_reopen(self):
        self.db.close()

        def take(_):
            with closing(sc.connect(self.path)) as db:
                return sd.submit(db, "task-a", 1, 1, 111)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            tickets = list(pool.map(take, range(12)))
        self.assertEqual(len(set(tickets)), 1)
        with closing(sc.connect(self.path)) as db:
            self.assertEqual(sd.states(db)["pending"], 1)


if __name__ == "__main__":
    unittest.main()
