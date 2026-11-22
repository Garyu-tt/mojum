"""CPU-only synthetic span tests; these do not invoke an actual model API."""
import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.spartan_inflight_meter import (
    begin, connect, create_schema, finish, heartbeat, snapshot, track_call,
)


class MeterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "meter.sqlite3")
        self.db = connect(self.path)
        create_schema(self.db)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_open_close_and_reported_usage(self):
        begin(self.db, 1, "request-1", "model.x", now_ms=100)
        self.assertEqual(snapshot(self.db, now_ms=101)["active"], 1)
        self.assertTrue(finish(self.db, 1, "request-1", "ok", now_ms=200,
                               input_tokens=12, output_tokens=7,
                               cost_microusd=89))
        self.assertFalse(finish(self.db, 1, "request-1", "ok", now_ms=201))
        state = snapshot(self.db, now_ms=202)
        self.assertEqual((state["active"], state["peak_active"], state["ok"]),
                         (0, 1, 1))
        self.assertEqual((state["reported_input_tokens"],
                          state["reported_output_tokens"],
                          state["reported_cost_microusd"]), (12, 7, 89))
        self.assertEqual(state["cost_reports"], 1)
        self.assertEqual(state["kind"], "instrumented_provider_rpc_spans")

    def test_single_active_per_worker_and_unique_request_id(self):
        begin(self.db, 1, "req-one", "model.x", now_ms=100)
        with self.assertRaises(sqlite3.IntegrityError):
            begin(self.db, 1, "req-two", "model.x", now_ms=101)
        with self.assertRaises(sqlite3.IntegrityError):
            begin(self.db, 2, "req-one", "model.x", now_ms=101)
        self.assertTrue(finish(self.db, 1, "req-one", "ok", now_ms=110))
        begin(self.db, 1, "req-three", "model.x", now_ms=111)

    def test_different_worker_cannot_close_or_extend(self):
        begin(self.db, 1, "req-one", "model.x", now_ms=100)
        self.assertFalse(finish(self.db, 2, "req-one", "ok", now_ms=101))
        self.assertFalse(heartbeat(self.db, 2, "req-one", now_ms=101))
        self.assertEqual(snapshot(self.db, now_ms=102)["active"], 1)

    def test_expired_call_cannot_reanimate_or_count_live(self):
        begin(self.db, 1, "req-one", "model.x", ttl_ms=30_000, now_ms=10)
        self.assertEqual(snapshot(self.db, now_ms=30_010)["expired"], 1)
        self.assertFalse(heartbeat(self.db, 1, "req-one", now_ms=30_011))
        self.assertFalse(finish(self.db, 1, "req-one", "ok", now_ms=30_012))
        begin(self.db, 1, "req-two", "model.x", now_ms=30_013)
        self.assertEqual(snapshot(self.db, now_ms=30_014)["active"], 1)

    def test_heartbeat_before_expiry_allows_finish(self):
        begin(self.db, 1, "req-one", "model.x", ttl_ms=30_000, now_ms=10)
        self.assertTrue(heartbeat(self.db, 1, "req-one",
                                  ttl_ms=30_000, now_ms=29_999))
        self.assertTrue(finish(self.db, 1, "req-one", "ok", now_ms=59_998))

    def test_clock_regression_cannot_forge_completion_or_shorten_lease(self):
        # Even trusted wall clocks can move backwards; records must never
        # claim a response before request start, nor reduce a live lease.
        begin(self.db, 1, "req-causal", "model.x", now_ms=10_000)
        self.assertFalse(heartbeat(self.db, 1, "req-causal", now_ms=9_999))
        self.assertFalse(finish(self.db, 1, "req-causal", "ok", now_ms=9_999))
        self.assertFalse(heartbeat(self.db, 1, "req-causal",
                                   ttl_ms=30_000, now_ms=10_001))
        self.assertEqual(snapshot(self.db, now_ms=10_002)["active"], 1)
        self.assertEqual(snapshot(self.db, now_ms=10_002)["ok"], 0)
        self.assertTrue(heartbeat(self.db, 1, "req-causal", now_ms=10_003))
        self.assertTrue(finish(self.db, 1, "req-causal", "ok", now_ms=10_004))
        self.assertEqual(snapshot(self.db, now_ms=10_005)["ok"], 1)

    def test_context_closes_exceptions_without_sensitive_messages(self):
        with track_call(self.db, 1, "success", "model.x") as done:
            done(input_tokens=8)
        with self.assertRaisesRegex(RuntimeError, "secret-message"):
            with track_call(self.db, 2, "failure", "model.x"):
                raise RuntimeError("secret-message")
        state = snapshot(self.db)
        self.assertEqual((state["ok"], state["failed"], state["active"]),
                         (1, 1, 0))
        self.assertEqual(state["cost_reports"], 0)  # unknown, not zero
        self.assertNotIn(b"secret-message", Path(self.path).read_bytes())

    def test_inputs_are_strict_and_failed_validation_does_not_write(self):
        for worker in (True, 0, 301, "1"):
            with self.assertRaises(ValueError):
                begin(self.db, worker, "req-one", "model.x", now_ms=10)
        for name in ("secret?token=abc", "bad\nname", "", "x" * 121):
            with self.assertRaises(ValueError):
                begin(self.db, 1, "req-one", name, now_ms=10)
        with self.assertRaises(ValueError):
            begin(self.db, 1, "req-one", "model.x", now_ms=True)
        with self.assertRaises(ValueError):
            begin(self.db, 1, "req-one", "model.x", ttl_ms=10, now_ms=10)
        begin(self.db, 1, "req-one", "model.x", now_ms=10)
        for value in (True, -1, 1_000_000_001):
            with self.assertRaises(ValueError):
                finish(self.db, 1, "req-one", "ok",
                       input_tokens=value, now_ms=11)
        self.assertEqual(snapshot(self.db, now_ms=12)["active"], 1)

    def test_300_concurrent_sqlite_starts_not_300_actual_model_calls(self):
        def run(worker):
            db = connect(self.path)
            try:
                begin(db, worker, f"request-{worker}", "model.x", now_ms=100)
            finally:
                db.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
            list(pool.map(run, range(1, 301)))
        self.assertEqual(snapshot(self.db, now_ms=101)["active"], 300)
        self.assertEqual(snapshot(self.db, now_ms=101)["peak_active"], 300)
        for worker in range(1, 301):
            self.assertTrue(finish(self.db, worker, f"request-{worker}",
                                   "ok", now_ms=200))
        self.assertEqual(snapshot(self.db, now_ms=201)["peak_active"], 300)


if __name__ == "__main__":
    unittest.main()
