import unittest

from mojum.external_agent_ingress_limit import IngressRateLimiter


class IngressRateLimiterTests(unittest.TestCase):
    def test_allows_limit_then_rejects_with_retry(self):
        limiter = IngressRateLimiter(limit=2, window_ns=10, max_sources=3)
        self.assertTrue(limiter.admit("agent-a", 0).allowed)
        second = limiter.admit("agent-a", 1)
        self.assertTrue(second.allowed)
        self.assertEqual(second.remaining, 0)
        denied = limiter.admit("agent-a", 2)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "rate_limited")
        self.assertEqual(denied.retry_after_ns, 8)

    def test_window_resets_at_exact_boundary(self):
        limiter = IngressRateLimiter(limit=1, window_ns=10)
        self.assertTrue(limiter.admit("agent-a", 0).allowed)
        self.assertFalse(limiter.admit("agent-a", 9).allowed)
        self.assertTrue(limiter.admit("agent-a", 10).allowed)

    def test_sources_are_isolated(self):
        limiter = IngressRateLimiter(limit=1, window_ns=10)
        self.assertTrue(limiter.admit("agent-a", 0).allowed)
        self.assertTrue(limiter.admit("agent-b", 1).allowed)

    def test_capacity_fails_closed_until_expiry(self):
        limiter = IngressRateLimiter(limit=1, window_ns=10, max_sources=1)
        self.assertTrue(limiter.admit("agent-a", 0).allowed)
        denied = limiter.admit("agent-b", 1)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "capacity_exhausted")
        self.assertEqual(limiter.tracked_sources, 1)
        self.assertTrue(limiter.admit("agent-b", 10).allowed)
        self.assertEqual(limiter.tracked_sources, 1)

    def test_capacity_retry_after_uses_earliest_expiry(self):
        limiter = IngressRateLimiter(limit=1, window_ns=10, max_sources=2)
        self.assertTrue(limiter.admit("agent-a", 0).allowed)
        self.assertTrue(limiter.admit("agent-b", 5).allowed)

        denied = limiter.admit("agent-c", 9)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "capacity_exhausted")
        self.assertEqual(denied.retry_after_ns, 1)

        # Once the earliest window expires, capacity becomes available.
        self.assertTrue(limiter.admit("agent-c", 10).allowed)

    def test_clock_rollback_does_not_mutate(self):
        limiter = IngressRateLimiter(limit=2, window_ns=10)
        self.assertTrue(limiter.admit("agent-a", 5).allowed)
        rollback = limiter.admit("agent-a", 4)
        self.assertEqual(rollback.reason, "clock_rollback")
        self.assertTrue(limiter.admit("agent-a", 6).allowed)

    def test_invalid_inputs_fail_closed(self):
        limiter = IngressRateLimiter()
        for source in ("", "../bad", "x" * 129, "white space", None, 3):
            self.assertFalse(limiter.admit(source, 0).allowed)
        for now in (True, -1, 1.5, "1", 2**63):
            self.assertEqual(limiter.admit("agent-a", now).reason, "invalid_clock")
        self.assertEqual(limiter.tracked_sources, 0)

    def test_constructor_rejects_bool_and_out_of_range(self):
        for kwargs in (
            {"limit": True}, {"window_ns": False}, {"max_sources": True},
            {"limit": 0}, {"window_ns": 0}, {"max_sources": 0},
        ):
            with self.assertRaises(ValueError):
                IngressRateLimiter(**kwargs)

    def test_expiry_pruning_is_bounded(self):
        limiter = IngressRateLimiter(limit=1, window_ns=5, max_sources=2)
        self.assertTrue(limiter.admit("a", 0).allowed)
        self.assertTrue(limiter.admit("b", 1).allowed)
        self.assertTrue(limiter.admit("c", 5).allowed)
        self.assertLessEqual(limiter.tracked_sources, 2)

    def test_concurrent_attempts_never_exceed_limit(self):
        import threading

        limiter = IngressRateLimiter(limit=7, window_ns=1000, max_sources=4)
        barrier = threading.Barrier(24)
        outcomes = []
        guard = threading.Lock()

        def attempt(index):
            barrier.wait()
            result = limiter.admit("shared-agent", 1)
            with guard:
                outcomes.append(result.allowed)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(outcomes), 7)


if __name__ == "__main__":
    unittest.main()
