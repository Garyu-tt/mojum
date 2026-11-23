import unittest
from datetime import datetime, timezone
from email.utils import format_datetime

from mojum.qa_poll_policy import (
    Circuit, PollPolicyError, backoff_delay, begin_probe, circuit_transition,
    parse_retry_after, poll_permitted,
)


class PollPolicyTests(unittest.TestCase):
    def test_exponential_growth_and_cap(self):
        self.assertEqual([backoff_delay(n) for n in range(5)], [2, 4, 8, 16, 32])
        self.assertEqual(backoff_delay(20), 300)

    def test_retry_after_delta_is_respected_and_capped(self):
        self.assertEqual(backoff_delay(1, retry_after="45"), 45)
        self.assertEqual(parse_retry_after(999999, now_epoch=0), 86400)

    def test_retry_after_http_date_uses_supplied_clock(self):
        target = datetime.fromtimestamp(150, timezone.utc)
        hint = format_datetime(target, usegmt=True)
        self.assertEqual(parse_retry_after(hint, now_epoch=100), 50)

    def test_invalid_retry_after_is_ignored(self):
        for value in ("", "-1", "NaN", "x" * 129, float("inf"), True):
            self.assertIsNone(parse_retry_after(value, now_epoch=0))

    def test_zero_failures_is_base_delay(self):
        self.assertEqual(backoff_delay(0), 2)

    def test_bad_failure_count_and_bounds_rejected(self):
        for value in (-1, 32, True, 1.5):
            with self.assertRaises(PollPolicyError):
                backoff_delay(value)
        with self.assertRaises(PollPolicyError):
            backoff_delay(1, max_seconds=1)
        for clock in (True, -1, float("nan"), float("inf"), "100"):
            with self.subTest(clock=clock):
                with self.assertRaises(PollPolicyError):
                    backoff_delay(1, now_epoch=clock)

    def test_transient_failure_opens_at_threshold(self):
        c = Circuit()
        for t in range(4):
            c = circuit_transition(c, "transient", now_epoch=t, failure_threshold=5)
        self.assertEqual(c.status, "closed")
        c = circuit_transition(c, "rate_limited", now_epoch=5, failure_threshold=5)
        self.assertEqual(c.status, "open")
        self.assertFalse(poll_permitted(c, now_epoch=64))
        self.assertTrue(poll_permitted(c, now_epoch=65))

    def test_single_half_open_probe(self):
        opened = Circuit(status="open", failures=5, opened_at=10)
        probe = begin_probe(opened, now_epoch=70)
        self.assertEqual(probe.status, "half_open")
        self.assertTrue(probe.probe_in_flight)
        self.assertFalse(poll_permitted(probe, now_epoch=71))
        with self.assertRaises(PollPolicyError):
            begin_probe(probe, now_epoch=71)

    def test_probe_failure_reopens_and_success_resets(self):
        half = Circuit(status="half_open", failures=5, opened_at=0, probe_in_flight=True)
        failed = circuit_transition(half, "transient", now_epoch=80)
        self.assertEqual(failed.status, "open")
        self.assertEqual(failed.opened_at, 80)
        self.assertEqual(circuit_transition(half, "success", now_epoch=81), Circuit())

    def test_forbidden_fails_closed_until_manual_or_timed_probe(self):
        c = circuit_transition(Circuit(), "forbidden", now_epoch=10)
        self.assertEqual(c.status, "open")
        self.assertFalse(poll_permitted(c, now_epoch=69))
        self.assertTrue(poll_permitted(c, now_epoch=70))

    def test_permanent_error_opens_circuit(self):
        self.assertEqual(circuit_transition(Circuit(), "permanent", now_epoch=1).status, "open")

    def test_invalid_clock_and_outcome_rejected(self):
        with self.assertRaises(PollPolicyError):
            circuit_transition(Circuit(), "success", now_epoch=float("nan"))
        with self.assertRaises(PollPolicyError):
            circuit_transition(Circuit(), "unknown", now_epoch=0)  # type: ignore[arg-type]

    def test_malformed_circuit_snapshots_are_rejected(self):
        invalid = (
            Circuit(status="unknown", opened_at=0),
            Circuit(status="closed", failures=True),
            Circuit(status="open", opened_at=float("nan")),
            Circuit(status="open", opened_at=-1),
            Circuit(status="open", opened_at=1, probe_in_flight=True),
            Circuit(status="closed", probe_in_flight=True),
        )
        for circuit in invalid:
            with self.subTest(circuit=circuit):
                with self.assertRaises(PollPolicyError):
                    poll_permitted(circuit, now_epoch=100)
                with self.assertRaises(PollPolicyError):
                    circuit_transition(circuit, "success", now_epoch=100)

    def test_retry_after_rejects_string_subclasses(self):
        class HostileString(str):
            def strip(self):
                raise AssertionError("subclass method must not run")

        self.assertIsNone(parse_retry_after(HostileString("10"), now_epoch=0))

    def test_closed_circuit_always_permits_poll(self):
        self.assertTrue(poll_permitted(Circuit(), now_epoch=0))


if __name__ == "__main__":
    unittest.main()
