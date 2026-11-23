import unittest

from mojum.qa_supervisor_metrics import MetricsInputError, summarize_snapshot


class SupervisorMetricsTests(unittest.TestCase):
    def test_aggregates_states_ages_blocks_and_leases(self):
        result = summarize_snapshot({
            "observed_at": 1000,
            "tasks": [
                {"id": "a", "state": "TESTING", "age_seconds": 20, "lease_remaining_seconds": 10},
                {"id": "b", "state": "BLOCKED_PERMISSION", "age_seconds": 4000, "blocked_reason": "PERMISSION"},
                {"id": "c", "state": "READY", "age_seconds": 90000},
                {"id": "d", "state": "CLAIMED"},
            ],
        })
        self.assertEqual(result["task_count"], 4)
        self.assertEqual(result["state_counts"]["TESTING"], 1)
        self.assertEqual(result["age_bucket_counts"], {"lt_5m": 1, "1h_to_1d": 1, "1d_to_7d": 1})
        self.assertEqual(result["blocked_reason_counts"], {"PERMISSION": 1})
        self.assertEqual(result["lease_health"], {"active": 0, "expiring": 1, "expired": 0, "missing": 1})

    def test_duration_nearest_rank(self):
        result = summarize_snapshot({"observed_at": 1, "tasks": [], "duration_samples_ms": [9, 1, 5, 3, 7]})
        self.assertEqual(result["duration_ms"], {"p50": 5.0, "p95": 9.0})

    def test_empty_samples_are_null(self):
        result = summarize_snapshot({"observed_at": 1, "tasks": []})
        self.assertEqual(result["duration_ms"], {"p50": None, "p95": None})

    def test_deterministic_digest_and_key_order(self):
        a = summarize_snapshot({"observed_at": 1, "tasks": [{"id": "x", "state": "READY"}]})
        b = summarize_snapshot({"tasks": [{"state": "READY", "id": "x"}], "observed_at": 1})
        self.assertEqual(a, b)

    def test_private_identifiers_are_not_emitted(self):
        result = summarize_snapshot({"observed_at": 1, "tasks": [{"id": "secret-host-pr-url", "state": "READY"}]})
        self.assertNotIn("secret-host-pr-url", repr(result))

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": 1, "tasks": [{"id": "x", "state": "READY"}, {"id": "x", "state": "MERGED"}]})

    def test_unknown_top_level_field_rejected(self):
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": 1, "tasks": [], "token": "no"})

    def test_unknown_task_field_rejected(self):
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": 1, "tasks": [{"id": "x", "state": "READY", "url": "private"}]})

    def test_unknown_state_rejected(self):
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": 1, "tasks": [{"id": "x", "state": "RUNNING"}]})

    def test_invalid_block_reason_rejected(self):
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": 1, "tasks": [{"id": "x", "state": "WAITING_CI", "blocked_reason": "arbitrary"}]})

    def test_reason_on_nonblocked_state_rejected(self):
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": 1, "tasks": [{"id": "x", "state": "READY", "blocked_reason": "OTHER"}]})

    def test_lease_outside_active_state_rejected(self):
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": 1, "tasks": [{"id": "x", "state": "READY", "lease_remaining_seconds": 5}]})

    def test_nonfinite_duration_rejected(self):
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": 1, "tasks": [], "duration_samples_ms": [float("nan")]})

    def test_boolean_not_accepted_as_integer(self):
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": True, "tasks": []})

    def test_task_limit_enforced(self):
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": 1, "tasks": [{"id": str(i), "state": "READY"} for i in range(10001)]})


if __name__ == "__main__":
    unittest.main()
