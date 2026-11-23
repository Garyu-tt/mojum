import builtins
import unittest
from unittest.mock import patch

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

    def test_hostile_snapshot_key_rejected_before_hash(self):
        class HostileKey(str):
            __hash__ = str.__hash__
            explode = False
            def __hash__(self):
                if self.explode:
                    raise RuntimeError("hostile hash executed")
                return str.__hash__(self)

        snapshot = {"observed_at": 1, "tasks": []}
        key = HostileKey("observed_at")
        snapshot[key] = snapshot.pop("observed_at")
        key.explode = True
        with self.assertRaises(MetricsInputError):
            summarize_snapshot(snapshot)

    def test_mapping_and_container_subclasses_rejected(self):
        class TaskDict(dict):
            pass
        class TaskList(list):
            pass

        with self.assertRaises(MetricsInputError):
            summarize_snapshot(TaskDict(observed_at=1, tasks=[]))
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": 1, "tasks": TaskList()})
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": 1, "tasks": [TaskDict(id="x", state="READY")]})

    def test_task_record_cardinality_and_native_key_types(self):
        class HostileKey(str):
            __hash__ = str.__hash__
            explode = False
            def __hash__(self):
                if self.explode:
                    raise RuntimeError("hostile hash executed")
                return str.__hash__(self)

        task = {"id": "x", "state": "READY"}
        key = HostileKey("id")
        task[key] = task.pop("id")
        key.explode = True
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": 1, "tasks": [task]})
        oversized = {"id": "x", "state": "READY"}
        oversized.update({f"extra-{i}": None for i in range(10_000)})
        with self.assertRaises(MetricsInputError):
            summarize_snapshot({"observed_at": 1, "tasks": [oversized]})

    def test_oversized_field_names_rejected_before_hashing(self):
        huge_key = "x" * 1_000_000
        snapshot = {"observed_at": 1, "tasks": [], huge_key: None}
        original_set = builtins.set

        def bounded_set(values):
            values = tuple(values)
            self.assertTrue(
                all(type(value) is str and len(value) <= 32 for value in values),
                "oversized field name reached set hashing",
            )
            return original_set(values)

        with patch("mojum.qa_supervisor_metrics.set",
                   side_effect=bounded_set, create=True):
            with self.assertRaises(MetricsInputError):
                summarize_snapshot(snapshot)

        task = {"id": "x", "state": "READY", huge_key: None}
        with patch("mojum.qa_supervisor_metrics.set",
                   side_effect=bounded_set,
                   create=True):
            with self.assertRaises(MetricsInputError):
                summarize_snapshot({"observed_at": 1, "tasks": [task]})


if __name__ == "__main__":
    unittest.main()
