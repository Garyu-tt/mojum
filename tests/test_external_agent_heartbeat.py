"""Offline, dependency-free #717 receipt admission and readback proofs."""

import os
import sqlite3
from pathlib import Path
import stat
import tempfile
import unittest

from mojum.external_agent_heartbeat import (
    ReceiptError, ReceiptJournal, SCHEMA, sign_receipt, validate_receipt,
)

KEY = b"fixture-only-not-a-production-secret--32++"
OTHER_KEY = b"an-unrelated-fixture-key-of-32-bytes++"


def event(*, kind="agent.started", seq=1, timestamp_ms=1_000_000, **change):
    row = {
        "schema": SCHEMA, "source": "chatgpt", "agent_id": "peer-1",
        "host": "cpu-node-1", "run_id": "run-1", "session_id": "session-1",
        "repo": "Garyu-tt/mojum", "provider": "cpu-local", "kind": kind,
        "seq": seq, "timestamp_ms": timestamp_ms, "ttl_ms": 30_000,
        "issue": 717, "pr": None, "sha": None,
        "status_code": "none" if kind in ("agent.started", "agent.heartbeat") else
            ("completed" if kind == "agent.finished" else "failed"),
    }
    row.update(change)
    return row


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "receipts.sqlite3"
        self.keys = {("chatgpt", "peer-1", "Garyu-tt/mojum"): KEY,
                     ("legion", "peer-2", "Garyu-tt/mojum"): OTHER_KEY}
        self.journal = ReceiptJournal(self.path, self.keys)
        # Resolve self.journal at cleanup time: restart/reopen tests replace it.
        # Capturing the original bound .close would leak the reopened SQLite DB.
        self.addCleanup(lambda: self.journal.close())

    def add(self, receipt, secret=KEY, now=1_000_000):
        return self.journal.ingest(receipt, sign_receipt(receipt, secret), observed_ms=now)

    def test_unknown_schedule_is_not_activity(self):
        state = self.journal.status("chatgpt", "peer-1", "scheduled-only", observed_ms=1_000_000)
        self.assertEqual(state["state"], "unknown")
        self.assertNotIn("observed_ms", state)

    def test_started_then_stale_exact_ttl(self):
        self.assertTrue(self.add(event()))
        self.assertEqual(self.journal.status("chatgpt", "peer-1", "run-1", observed_ms=1_029_999)["state"], "active")
        stale = self.journal.status("chatgpt", "peer-1", "run-1", observed_ms=1_030_000)
        self.assertEqual(stale["state"], "stale")
        self.assertEqual(stale["age_ms"], 30_000)

    def test_heartbeat_refreshes_liveness(self):
        self.add(event())
        self.add(event(kind="agent.heartbeat", seq=2, timestamp_ms=1_010_000), now=1_010_000)
        self.assertEqual(self.journal.status("chatgpt", "peer-1", "run-1", observed_ms=1_039_999)["state"], "active")
        self.assertEqual(self.journal.status("chatgpt", "peer-1", "run-1", observed_ms=1_040_000)["state"], "stale")

    def test_terminal_completed_never_becomes_stale(self):
        self.add(event())
        self.add(event(kind="agent.finished", seq=2, timestamp_ms=1_001_000), now=1_001_000)
        result = self.journal.status("chatgpt", "peer-1", "run-1", observed_ms=7_000_000)
        self.assertEqual(result["state"], "finished")
        self.assertEqual(result["status_code"], "completed")

    def test_failed_diagnostic_redacted(self):
        self.add(event())
        self.add(event(kind="agent.failed", seq=2, timestamp_ms=1_001_000,
                       status_code="rate_limited"), now=1_001_000)
        result = self.journal.status("chatgpt", "peer-1", "run-1", observed_ms=7_000_000)
        self.assertEqual((result["state"], result["status_code"]), ("failed", "rate_limited"))
        self.assertNotIn("diagnostic_text", result)

    def test_duplicate_is_idempotent_and_never_refreshes_liveness(self):
        receipt = event()
        self.assertTrue(self.add(receipt))
        self.assertFalse(self.add(receipt, now=1_025_000))
        self.assertEqual(self.journal.status("chatgpt", "peer-1", "run-1", observed_ms=1_030_000)["state"], "stale")

    def test_conflicting_same_sequence_is_rejected(self):
        self.add(event())
        with self.assertRaisesRegex(ReceiptError, "conflicting sequence"):
            self.add(event(host="cpu-node-2"))
        self.assertEqual(self.journal.status("chatgpt", "peer-1", "run-1", observed_ms=1_000_001)["seq"], 1)

    def test_wrong_signature_and_unknown_source(self):
        receipt = event()
        with self.assertRaisesRegex(ReceiptError, "signature"):
            self.add(receipt, secret=OTHER_KEY)
        with self.assertRaisesRegex(ReceiptError, "unknown authenticated"):
            self.add(event(source="untrusted"))
        self.assertEqual(self.journal.status("chatgpt", "peer-1", "run-1", observed_ms=1_000_000)["state"], "unknown")

    def test_repo_scoped_enrollment(self):
        with self.assertRaisesRegex(ReceiptError, "unknown authenticated"):
            self.add(event(repo="Other/project"))
        self.assertEqual(self.journal.readback(observed_ms=1_000_000)["runs"], [])

    def test_readback_is_bounded_and_does_not_infer_workers(self):
        self.add(event())
        self.add(event(run_id="run-2"))
        result = self.journal.readback(observed_ms=1_020_000, limit=1)
        self.assertEqual(len(result["runs"]), 1)
        self.assertTrue(result["truncated"])
        self.assertFalse(self.journal.readback(observed_ms=1_020_000)["truncated"])
        self.assertEqual(len(self.journal.readback(observed_ms=1_020_000)["runs"]), 2)
        with self.assertRaises(ReceiptError):
            self.journal.readback(observed_ms=1_020_000, limit=0)

    def test_replay_with_invalid_signature_cannot_pass_as_duplicate(self):
        receipt = event()
        self.add(receipt)
        with self.assertRaisesRegex(ReceiptError, "signature"):
            self.journal.ingest(receipt, "0" * 64, observed_ms=1_000_001)

    def test_gaps_and_heartbeat_before_start(self):
        with self.assertRaisesRegex(ReceiptError, "first event"):
            self.add(event(kind="agent.heartbeat"))
        with self.assertRaisesRegex(ReceiptError, "first event"):
            self.add(event(seq=2))
        self.add(event())
        with self.assertRaisesRegex(ReceiptError, "sequence gap"):
            self.add(event(kind="agent.heartbeat", seq=3))

    def test_new_event_after_terminal_rejected(self):
        self.add(event())
        self.add(event(kind="agent.finished", seq=2))
        with self.assertRaisesRegex(ReceiptError, "terminal"):
            self.add(event(kind="agent.heartbeat", seq=3))

    def test_run_identity_must_be_stable(self):
        self.add(event())
        for change in ({"host": "host-moved"}, {"session_id": "session-2"},
                       {"provider": "cloud"}, {"repo": "Other/project"}):
            with self.subTest(change=change), self.assertRaisesRegex(ReceiptError, "identity"):
                self.add(event(kind="agent.heartbeat", seq=2, **change))

    def test_nonmonotonic_timestamp_and_server_clock(self):
        self.add(event())
        with self.assertRaisesRegex(ReceiptError, "nonmonotonic time"):
            self.add(event(kind="agent.heartbeat", seq=2, timestamp_ms=999_999), now=1_000_001)
        with self.assertRaisesRegex(ReceiptError, "nonmonotonic time"):
            self.add(event(kind="agent.heartbeat", seq=2), now=999_999)

    def test_reject_far_future_and_ancient_new_receipts(self):
        with self.assertRaisesRegex(ReceiptError, "admission window"):
            self.add(event(timestamp_ms=1_301_001))
        with self.assertRaisesRegex(ReceiptError, "admission window"):
            self.add(event(timestamp_ms=699_999))

    def test_restart_restores_verified_state_and_sequence(self):
        self.add(event())
        self.journal.close()
        self.journal = ReceiptJournal(self.path, self.keys)
        self.assertEqual(self.journal.status("chatgpt", "peer-1", "run-1", observed_ms=1_030_000)["state"], "stale")
        self.assertFalse(self.add(event(), now=1_040_000))
        self.assertTrue(self.add(event(kind="agent.heartbeat", seq=2, timestamp_ms=1_040_000), now=1_040_000))
        self.assertEqual(self.journal.status("chatgpt", "peer-1", "run-1", observed_ms=1_050_000)["state"], "active")

    def test_unittest_cleanup_closes_reopened_journal(self):
        """Regression: cleanup must close the replacement, not only the old DB."""
        self.journal.close()
        self.journal = ReceiptJournal(self.path, self.keys)
        self.doCleanups()
        with self.assertRaises(sqlite3.ProgrammingError):
            self.journal.readback(observed_ms=1_000_000)

    def test_multi_source_and_parallel_runs_isolated(self):
        self.add(event())
        legion = event(source="legion", agent_id="peer-2", run_id="run-1")
        self.add(legion, secret=OTHER_KEY)
        self.add(event(run_id="run-2"))
        self.add(event(kind="agent.finished", seq=2))
        self.assertEqual(self.journal.status("chatgpt", "peer-1", "run-1", observed_ms=1_035_000)["state"], "finished")
        self.assertEqual(self.journal.status("legion", "peer-2", "run-1", observed_ms=1_035_000)["state"], "stale")
        self.assertEqual(self.journal.status("chatgpt", "peer-1", "run-2", observed_ms=1_035_000)["state"], "stale")

    def test_no_raw_secrets_persisted(self):
        receipt = event()
        self.add(receipt)
        self.journal.close()
        payload = self.path.read_bytes()
        self.assertNotIn(KEY, payload)
        self.assertNotIn(OTHER_KEY, payload)
        self.assertNotIn(sign_receipt(receipt, KEY).encode(), payload)
        self.journal = ReceiptJournal(self.path, self.keys)

    def test_private_permissions(self):
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_reject_symlink_and_world_readable_database(self):
        linked = self.path.parent / "alias.sqlite3"
        linked.symlink_to(self.path)
        with self.assertRaises(ReceiptError):
            ReceiptJournal(linked, self.keys)
        self.journal.close()
        os.chmod(self.path, 0o644)
        with self.assertRaises(ReceiptError):
            ReceiptJournal(self.path, self.keys)
        os.chmod(self.path, 0o600)
        self.journal = ReceiptJournal(self.path, self.keys)

    def test_reject_relative_file_path(self):
        with self.assertRaisesRegex(ReceiptError, "absolute"):
            ReceiptJournal("unsafe.sqlite3", self.keys)

    def test_invalid_signature_format(self):
        with self.assertRaisesRegex(ReceiptError, "invalid signature"):
            self.journal.ingest(event(), "not-a-signature", observed_ms=1_000_000)

    def test_query_time_must_not_predate_observation(self):
        self.add(event())
        with self.assertRaisesRegex(ReceiptError, "query time"):
            self.journal.status("chatgpt", "peer-1", "run-1", observed_ms=999_999)

    def test_refuse_undeclared_fields_and_freeform_logs(self):
        with self.assertRaises(ReceiptError):
            validate_receipt(event(diagnostic_text="secret=PASSWORD"))
        with self.assertRaises(ReceiptError):
            validate_receipt(event(token="super-secret"))

    def test_reject_malformed_scalar_types_and_protocols(self):
        changes = (
            {"seq": True}, {"ttl_ms": -1}, {"timestamp_ms": float("nan")},
            {"schema": "mojum.external-agent-receipt/2"}, {"sha": "f" * 39},
            {"repo": "../private"}, {"source": "../../rpc"},
            {"issue": True}, {"run_id": "line\nbreak"},
            {"status_code": "traceback: auth token"},
            {"status_code": "completed"},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ReceiptError):
                validate_receipt(event(**change))

    def test_terminal_kind_and_status_are_typed(self):
        for item in (event(kind="agent.finished", status_code="none"),
                     event(kind="agent.failed", status_code="completed"),
                     event(kind="agent.heartbeat", status_code="unreachable")):
            with self.assertRaises(ReceiptError):
                validate_receipt(item)

    def test_bad_attempt_does_not_poison_valid_next_sequence(self):
        self.add(event())
        with self.assertRaises(ReceiptError):
            self.add(event(kind="agent.heartbeat", seq=3))
        self.assertTrue(self.add(event(kind="agent.heartbeat", seq=2)))


if __name__ == "__main__":
    unittest.main()
