import copy
import math
import unittest

from mojum.pettingzoo_episode import (
    SCHEMA, PROVENANCE, admit_episode, EpisodeReceipt,
)


def sample():
    return {
        "schema": SCHEMA,
        "provenance": PROVENANCE,
        "seed": 7,
        "agents": ["b", "a"],
        "steps": [{
            "tick": 0,
            "observations": {"a": [0.0, 1], "b": [2, 3]},
            "actions": {"a": 1, "b": [0, 1]},
            "rewards": {"a": 0.5, "b": -0.5},
            "terminations": {"a": False, "b": False},
            "truncations": {"a": False, "b": False},
        }, {
            "tick": 3,
            "observations": {"a": [1.0, 1], "b": [2, 2]},
            "actions": {"a": 0, "b": 1},
            "rewards": {"a": 1.0, "b": 0.0},
            "terminations": {"a": True, "b": False},
            "truncations": {"a": False, "b": True},
        }],
    }


class PettingZooEpisodeTests(unittest.TestCase):
    def test_valid_trace_is_detached_and_immutable(self):
        source = sample()
        receipt = admit_episode(source)
        self.assertIsInstance(receipt, EpisodeReceipt)
        self.assertEqual(receipt.agents, ("a", "b"))
        self.assertEqual(receipt.step_count, 2)
        self.assertEqual(receipt.steps[0][1][0][1], (0.0, 1))
        source["steps"][0]["observations"]["a"][0] = 999
        self.assertEqual(receipt.steps[0][1][0][1], (0.0, 1))
        with self.assertRaises((AttributeError, TypeError)):
            receipt.seed = 8

    def test_digest_is_order_independent_for_agent_maps(self):
        first = sample()
        second = sample()
        second["agents"].reverse()
        for step in second["steps"]:
            for field in ("observations", "actions", "rewards",
                          "terminations", "truncations"):
                step[field] = dict(reversed(list(step[field].items())))
        self.assertEqual(admit_episode(first).digest, admit_episode(second).digest)

    def test_digest_changes_when_content_changes(self):
        first = sample()
        second = sample()
        second["steps"][0]["rewards"]["a"] = 0.25
        self.assertNotEqual(admit_episode(first).digest, admit_episode(second).digest)

    def test_rejects_extra_missing_and_unknown_root_fields(self):
        for mutate in (
            lambda x: x.pop("seed"),
            lambda x: x.update(extra=True),
            lambda x: x.update(provenance="PRIVATE"),
        ):
            value = sample()
            mutate(value)
            with self.assertRaises(ValueError):
                admit_episode(value)

    def test_rejects_bad_agent_inventory(self):
        for agents in ([], ["a", "a"], ["a", 3], ["x" * 65], ["a"] * 65):
            value = sample()
            value["agents"] = agents
            with self.assertRaises(ValueError):
                admit_episode(value)

    def test_agent_maps_must_be_exact(self):
        for field in ("observations", "actions", "rewards",
                      "terminations", "truncations"):
            value = sample()
            value["steps"][0][field].pop("a")
            with self.assertRaises(ValueError):
                admit_episode(value)
            value = sample()
            value["steps"][0][field]["rogue"] = 0
            with self.assertRaises(ValueError):
                admit_episode(value)

    def test_rejects_bad_ticks(self):
        for ticks in ((0, 0), (1, 0), (False, 1), (-1, 2), (0, 2**63)):
            value = sample()
            for step, tick in zip(value["steps"], ticks):
                step["tick"] = tick
            with self.assertRaises(ValueError):
                admit_episode(value)

    def test_rejects_non_finite_and_bool_numeric_values(self):
        for bad in (float("nan"), float("inf"), True, "1"):
            value = sample()
            value["steps"][0]["observations"]["a"] = [bad]
            with self.assertRaises(ValueError):
                admit_episode(value)
        value = sample()
        value["steps"][0]["rewards"]["a"] = -math.inf
        with self.assertRaises(ValueError):
            admit_episode(value)
        value = sample()
        value["steps"][0]["rewards"]["a"] = 10 ** 1000
        with self.assertRaises(ValueError):
            admit_episode(value)

    def test_rejects_bad_flags_and_vector_bounds(self):
        value = sample()
        value["steps"][0]["terminations"]["a"] = 1
        with self.assertRaises(ValueError):
            admit_episode(value)
        value = sample()
        value["steps"][0]["actions"]["a"] = [0] * 257
        with self.assertRaises(ValueError):
            admit_episode(value)

    def test_rejects_hostile_container_subclasses(self):
        class Dict(dict):
            pass
        class List(list):
            pass
        value = sample()
        value["steps"][0]["observations"] = Dict(value["steps"][0]["observations"])
        with self.assertRaises(ValueError):
            admit_episode(value)
        value = sample()
        value["agents"] = List(value["agents"])
        with self.assertRaises(ValueError):
            admit_episode(value)

    def test_input_is_not_mutated(self):
        value = sample()
        before = copy.deepcopy(value)
        admit_episode(value)
        self.assertEqual(value, before)

    def test_step_and_agent_limits(self):
        value = sample()
        value["steps"] = value["steps"] * 5001
        with self.assertRaises(ValueError):
            admit_episode(value)
        value = sample()
        value["seed"] = True
        with self.assertRaises(ValueError):
            admit_episode(value)


    def test_rejects_aggregate_scalar_budget_before_canonicalization(self):
        value = sample()
        template = value["steps"][0]
        value["steps"] = []
        for tick in range(100):
            step = copy.deepcopy(template)
            step["tick"] = tick
            for field in ("observations", "actions"):
                step[field] = {agent: [0] * 256 for agent in value["agents"]}
            value["steps"].append(step)
        with self.assertRaisesRegex(ValueError, "aggregate scalar budget"):
            admit_episode(value)

    def test_rejects_hostile_string_subclass_keys_before_hashing(self):
        class HostileKey(str):
            armed = False
            def __hash__(self):
                if self.armed:
                    raise AssertionError("hostile hash executed")
                return str.__hash__(self)
            def __eq__(self, other):
                if self.armed:
                    raise AssertionError("hostile equality executed")
                return str.__eq__(self, other)

        for target in ("root", "step", "agent_map"):
            value = sample()
            if target == "root":
                original = "schema"
                mapping = value
            elif target == "step":
                original = "tick"
                mapping = value["steps"][0]
            else:
                original = "a"
                mapping = value["steps"][0]["observations"]
            key = HostileKey(original)
            mapping[key] = mapping.pop(original)
            key.armed = True
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    admit_episode(value)

    def test_rejects_non_native_schema_and_provenance_before_comparison(self):
        class HostileValue:
            def __eq__(self, other):
                raise AssertionError("hostile equality executed")
        for field in ("schema", "provenance"):
            value = sample()
            value[field] = HostileValue()
            with self.assertRaises(ValueError):
                admit_episode(value)


if __name__ == "__main__":
    unittest.main()
