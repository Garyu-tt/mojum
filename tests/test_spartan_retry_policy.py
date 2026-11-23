"""Hermetic, stdlib-only adversarial tests for independent Spartan retry policy."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mojum.spartan_retry_policy import (
    RETRYABLE_STATUSES, SCHEMA, RetryPolicy, plan_retry,
)

NOW = datetime(2026, 11, 23, 16, 0, 0, 500_000, tzinfo=timezone.utc)


class RetryPolicyTests(unittest.TestCase):
    def decide(self, **overrides):
        args = dict(status_code=429, failed_attempt=1, now_utc=NOW,
                    replay_safe=True)
        args.update(overrides)
        return plan_retry(**args)

    def test_defaults_and_schema(self):
        result = self.decide()
        self.assertTrue(result.should_retry)
        self.assertEqual((result.delay_ms, result.reason, result.schema),
                         (250, "retry_allowed", SCHEMA))
        with self.assertRaises(AttributeError):
            result.delay_ms = 123

    def test_exponential_backoff_and_cap(self):
        cfg = RetryPolicy(max_attempts=9, initial_delay_ms=250,
                          max_backoff_ms=1_000)
        expected = (250, 500, 1_000, 1_000, 1_000, 1_000, 1_000, 1_000)
        for attempt, ms in enumerate(expected, 1):
            with self.subTest(attempt=attempt):
                self.assertEqual(self.decide(failed_attempt=attempt, policy=cfg).delay_ms, ms)
        self.assertEqual(self.decide(failed_attempt=9, policy=cfg).reason,
                         "attempts_exhausted")

    def test_only_explicit_retryable_statuses(self):
        for status in range(100, 600):
            decision = self.decide(status_code=status)
            self.assertEqual(decision.should_retry, status in RETRYABLE_STATUSES)

    def test_idempotency_required_for_any_retry(self):
        for code in RETRYABLE_STATUSES:
            self.assertEqual(self.decide(status_code=code, replay_safe=False).reason,
                             "unsafe_replay")

    def test_retry_after_delta_seconds(self):
        self.assertEqual(self.decide(retry_after="002").delay_ms, 2_000)
        self.assertEqual(self.decide(retry_after="0").delay_ms, 250)
        self.assertEqual(self.decide(retry_after="120").delay_ms, 120_000)
        self.assertEqual(self.decide(retry_after="121").reason,
                         "retry_after_exceeds_limit")

    def test_retry_after_date_exact_ceiling(self):
        self.assertEqual(self.decide(retry_after="Mon, 23 Nov 2026 16:00:02 GMT").delay_ms,
                         1_500)
        self.assertEqual(self.decide(retry_after="Mon, 23 Nov 2026 15:00:00 GMT").delay_ms,
                         250)
        self.assertEqual(self.decide(retry_after="Mon, 23 Nov 2026 16:00:01 GMT").delay_ms,
                         500)

    def test_malformed_header_is_not_silently_ignored(self):
        bad = ["", " ", " 5", "5 ", "+5", "-5", "5.0", "1\r\nX-Other: yes",
               "99999999999", "١٠", "1, 2", "Mon, 23 Nov 2026 16:00:02 UTC",
               "Tue, 23 Nov 2026 16:00:02 GMT", "Sunday, 23-Nov-26 16:00:02 GMT",
               "Mon, 32 Nov 2026 16:00:02 GMT", "0" * 65,
               "Mon, 23 Nov 2026 16:00:02 gmt", "Mon, 23 Nov 2026 16:00:02 GMT "]
        for header in bad:
            with self.subTest(header=header):
                decision = self.decide(retry_after=header)
                self.assertEqual((decision.should_retry, decision.reason),
                                 (False, "invalid_retry_after"))

    def test_policy_rejects_bad_types_and_limits(self):
        for params in [dict(max_attempts=True), dict(max_attempts=0),
                       dict(max_attempts=33), dict(initial_delay_ms=0),
                       dict(initial_delay_ms=1.2),
                       dict(initial_delay_ms=2_000, max_backoff_ms=1_000),
                       dict(max_retry_after_ms=86_400_001),
                       dict(max_backoff_ms=False)]:
            with self.subTest(params=params), self.assertRaises(ValueError):
                RetryPolicy(**params)

    def test_arguments_are_native_and_finite(self):
        wrong = [dict(status_code=True), dict(status_code=600),
                 dict(failed_attempt=False), dict(failed_attempt=0),
                 dict(failed_attempt=6), dict(now_utc=NOW.replace(tzinfo=None)),
                 dict(now_utc=NOW.astimezone(timezone(timedelta(hours=2)))),
                 dict(now_utc="2026-11-23"), dict(replay_safe=1),
                 dict(retry_after=b"3"), dict(policy=None),
                 dict(deadline_utc=NOW.replace(tzinfo=None)),
                 dict(remaining_wait_ms=-1), dict(remaining_wait_ms=1.5),
                 dict(remaining_wait_ms=True),
                 dict(remaining_wait_ms=86_400_001)]
        for arg in wrong:
            with self.subTest(arg=arg), self.assertRaises(ValueError):
                self.decide(**arg)

    def test_deadline_exact_and_insufficient(self):
        self.assertEqual(self.decide(deadline_utc=NOW + timedelta(milliseconds=250)).delay_ms,
                         250)
        self.assertEqual(self.decide(deadline_utc=NOW + timedelta(milliseconds=249)).reason,
                         "deadline_exceeded")
        self.assertEqual(self.decide(deadline_utc=NOW - timedelta(seconds=1)).reason,
                         "deadline_exceeded")

    def test_wait_budget_honors_server_delay(self):
        self.assertEqual(self.decide(remaining_wait_ms=249).reason, "wait_budget_exceeded")
        self.assertEqual(self.decide(retry_after="3", remaining_wait_ms=1_000).reason,
                         "wait_budget_exceeded")
        self.assertTrue(self.decide(retry_after="3", remaining_wait_ms=3_000).should_retry)

    def test_large_retry_after_refused_not_capped_early(self):
        self.assertEqual(self.decide(retry_after="9999999999").reason,
                         "retry_after_exceeds_limit")
        self.assertEqual(self.decide(retry_after="Fri, 01 Jan 2100 00:00:00 GMT").reason,
                         "retry_after_exceeds_limit")

    def test_clock_overflow(self):
        last = datetime.max.replace(tzinfo=timezone.utc)
        self.assertEqual(self.decide(now_utc=last).reason, "clock_overflow")

    def test_no_implicit_sleep_network_or_clock(self):
        with patch("time.sleep", side_effect=AssertionError("sleep")), \
             patch("datetime.datetime", wraps=datetime):
            self.assertEqual(self.decide(retry_after="5").delay_ms, 5_000)

    def test_deterministic_repeated_result(self):
        first = self.decide(retry_after="Mon, 23 Nov 2026 16:00:02 GMT")
        self.assertTrue(all(self.decide(retry_after="Mon, 23 Nov 2026 16:00:02 GMT") == first
                            for _ in range(100)))

    def test_policy_does_not_override_replay_safety(self):
        self.assertEqual(self.decide(retry_after="bogus", replay_safe=False).reason,
                         "unsafe_replay")


if __name__ == "__main__":
    unittest.main()
