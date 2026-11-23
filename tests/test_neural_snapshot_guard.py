import unittest

from mojum.neural import NeuralError, PulsePredictor
from mojum.neural_snapshot_guard import _MAX_NODES, restore_snapshot


class HostileDict(dict):
    def __iter__(self):
        raise RuntimeError("hostile iteration")

    def items(self):
        raise RuntimeError("hostile items")


class HostileList(list):
    def __iter__(self):
        raise RuntimeError("hostile iteration")


class HostileString(str):
    def __eq__(self, other):
        raise RuntimeError("hostile equality")


def fixture():
    predictor = PulsePredictor()
    predictor.observe({
        "kind": "interoception",
        "observed_at_ns": 0,
        "heartbeat": {"phase_milli": 0, "pulse": True, "synthetic": True},
    })
    return predictor.snapshot()


class NeuralSnapshotGuardTests(unittest.TestCase):
    def test_valid_snapshot_roundtrips_and_is_detached(self):
        state = fixture()
        restored = restore_snapshot(state)
        self.assertEqual(restored.snapshot(), state)
        state["weights"][0] = 99
        self.assertNotEqual(restored.snapshot()["weights"][0], 99)

    def test_accepts_compensated_rule_only_when_explicit(self):
        restored = restore_snapshot(fixture(), summation="compensated")
        self.assertEqual(restored.updates, 0)

    def test_rejects_top_level_dict_subclass_without_running_hooks(self):
        with self.assertRaises(NeuralError):
            restore_snapshot(HostileDict(fixture()))

    def test_rejects_nested_list_subclass_without_running_hooks(self):
        state = fixture()
        state["weights"] = HostileList(state["weights"])
        with self.assertRaises(NeuralError):
            restore_snapshot(state)

    def test_rejects_string_subclass_before_schema_comparison(self):
        state = fixture()
        state["schema"] = HostileString(state["schema"])
        with self.assertRaises(NeuralError):
            restore_snapshot(state)

    def test_rejects_non_string_key(self):
        state = fixture()
        state[1] = "unexpected"
        with self.assertRaises(NeuralError):
            restore_snapshot(state)

    def test_rejects_cycle_and_shared_nested_container(self):
        state = fixture()
        state["pending_features"] = []
        state["weights"] = state["pending_features"]
        with self.assertRaises(NeuralError):
            restore_snapshot(state)
        state = fixture()
        state["weights"].append(state["weights"])
        with self.assertRaises(NeuralError):
            restore_snapshot(state)

    def test_rejects_oversized_integer_and_nonfinite(self):
        state = fixture()
        state["updates"] = 1 << 200
        with self.assertRaises(NeuralError):
            restore_snapshot(state)
        state = fixture()
        state["loss_sum"] = float("inf")
        with self.assertRaises(NeuralError):
            restore_snapshot(state)

    def test_rejects_unknown_fields_and_schema(self):
        state = fixture()
        state["extra"] = 1
        with self.assertRaises(NeuralError):
            restore_snapshot(state)
        state = fixture()
        state["schema"] = "mojum.neural.hidden/2"
        with self.assertRaises(NeuralError):
            restore_snapshot(state)

    def test_rejects_oversized_container_cardinality_before_expansion(self):
        state = fixture()
        state["weights"] = [0] * (_MAX_NODES + 1)
        with self.assertRaisesRegex(NeuralError, "structural budget"):
            restore_snapshot(state)

        state = fixture()
        state.update({f"extra_{index}": 0 for index in range(_MAX_NODES)})
        with self.assertRaisesRegex(NeuralError, "structural budget"):
            restore_snapshot(state)

    def test_rejects_invalid_summation_before_restore(self):
        with self.assertRaises(NeuralError):
            restore_snapshot(fixture(), summation=HostileString("ordered"))


if __name__ == "__main__":
    unittest.main()
