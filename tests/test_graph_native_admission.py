"""Adversarial direct-Python admission tests for executable Graph v1."""
import copy
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from mojum.graph import (
    GraphError,
    MAX_GRAPH_BYTES,
    MAX_GRAPH_TREE_NODES,
    SCHEMA,
    validate,
    _require_native_json_tree,
)
from mojum.sim import digest


FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "examples" / "first_loop.json").read_text(encoding="utf-8")
)


class HostileString(str):
    def __hash__(self):
        return str.__hash__(self)

    def __eq__(self, other):
        raise AssertionError("hostile string equality was invoked")


class HostileDict(dict):
    def items(self):
        raise AssertionError("hostile dict iteration was invoked")

    def get(self, key, default=None):
        raise AssertionError("hostile dict lookup was invoked")


class HostileList(list):
    def __iter__(self):
        raise AssertionError("hostile list iteration was invoked")


class GraphNativeAdmissionTests(unittest.TestCase):
    def fresh(self):
        return copy.deepcopy(FIXTURE)

    def test_canonical_graph_preserves_hash_and_topological_order(self):
        graph = self.fresh()
        before = digest(graph)
        nodes, incoming, order = validate(graph)
        self.assertEqual(len(nodes), 6)
        self.assertEqual(len(order), 6)
        self.assertTrue(incoming)
        self.assertEqual(digest(graph), before)
        self.assertEqual(graph, FIXTURE)

    def test_root_container_subclass_is_rejected_without_custom_lookup(self):
        class Root(HostileDict):
            pass

        with self.assertRaises(GraphError):
            validate(Root(self.fresh()))

    def test_nested_mapping_and_sequence_subclasses_are_rejected(self):
        graph = self.fresh()
        graph["nodes"][0]["config"] = HostileDict()
        with self.assertRaises(GraphError):
            validate(graph)

        graph = self.fresh()
        graph["nodes"][0]["config"]["extra"] = HostileList([1, 2])
        with self.assertRaises(GraphError):
            validate(graph)

    def test_hostile_string_subclass_key_is_rejected_before_set_comparison(self):
        graph = self.fresh()
        graph[HostileString("untrusted-extra")] = "x"
        with self.assertRaises(GraphError):
            validate(graph)

    def test_hostile_string_subclass_value_is_rejected(self):
        graph = self.fresh()
        graph["nodes"][0]["notes"]["purpose"] = HostileString("synthetic")
        with self.assertRaises(GraphError):
            validate(graph)

    def test_hostile_key_inside_extensible_notes_is_rejected(self):
        graph = self.fresh()
        graph["nodes"][0]["notes"][HostileString("annotation")] = "value"
        with self.assertRaises(GraphError):
            validate(graph)

    def test_tuple_set_bytes_and_custom_objects_are_not_json_values(self):
        for value in ((1, 2), {1, 2}, b"bytes", object()):
            with self.subTest(value_type=type(value).__name__):
                graph = self.fresh()
                graph["nodes"][0]["config"]["extra"] = value
                with self.assertRaises(GraphError):
                    validate(graph)

    def test_nonfinite_float_is_rejected_at_direct_validation_boundary(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            graph = self.fresh()
            graph["nodes"][0]["config"]["extra"] = value
            with self.subTest(value=value), self.assertRaises(GraphError):
                validate(graph)

    def test_unpaired_surrogate_is_rejected_as_graph_error(self):
        graph = self.fresh()
        graph["nodes"][0]["config"]["extra"] = "\ud800"
        with self.assertRaises(GraphError):
            validate(graph)

    def test_cycle_and_shared_containers_are_rejected(self):
        graph = self.fresh()
        graph["nodes"][0]["config"]["self"] = graph
        with self.assertRaises(GraphError):
            validate(graph)

        graph = self.fresh()
        shared = {"purpose": "shared synthetic metadata"}
        graph["nodes"][0]["config"] = shared
        graph["nodes"][0]["notes"] = shared
        with self.assertRaises(GraphError):
            validate(graph)

    def test_pending_work_is_bounded_before_expanding_nested_aliases(self):
        shared = []
        for _ in range(16):
            shared = [shared] * 16
        value = {"nested": shared}
        with patch("mojum.graph.MAX_GRAPH_TREE_NODES", 128):
            # The budget guard rejects scheduled work before walking down the
            # repeated-reference chain and accumulating a large pending stack.
            with self.assertRaisesRegex(GraphError, "node budget"):
                _require_native_json_tree(value)

    def test_direct_input_obeys_json_depth_budget(self):
        graph = self.fresh()
        nested = "leaf"
        for _ in range(257):
            nested = [nested]
        graph["nodes"][0]["config"]["deep"] = nested
        with self.assertRaisesRegex(GraphError, "nesting depth"):
            validate(graph)

    def test_direct_input_obeys_aggregate_node_budget(self):
        graph = self.fresh()
        graph["nodes"][0]["config"]["bulk"] = [None] * MAX_GRAPH_TREE_NODES
        with self.assertRaisesRegex(GraphError, "node budget"):
            validate(graph)

    def test_direct_input_obeys_utf8_byte_budget(self):
        graph = self.fresh()
        graph["nodes"][0]["config"]["bulk"] = "x" * (MAX_GRAPH_BYTES + 1)
        with self.assertRaisesRegex(GraphError, "256 KiB"):
            validate(graph)

    def test_escaped_json_size_is_rejected_before_serialization(self):
        graph = self.fresh()
        # Each control character expands to six bytes in compact JSON.
        graph["nodes"][0]["config"]["control"] = "\x01" * (MAX_GRAPH_BYTES // 6 + 1)
        with patch("mojum.graph.json.dumps", wraps=json.dumps) as serializer:
            with self.assertRaisesRegex(GraphError, "256 KiB"):
                validate(graph)
            serializer.assert_not_called()

    def test_json_structure_overhead_is_included_in_preflight(self):
        graph = self.fresh()
        graph["nodes"][0]["config"]["padding"] = "x" * (MAX_GRAPH_BYTES - 220)
        graph["nodes"][0]["config"]["many"] = [None] * 100
        with patch("mojum.graph.json.dumps", wraps=json.dumps) as serializer:
            with self.assertRaisesRegex(GraphError, "256 KiB"):
                validate(graph)
            serializer.assert_not_called()

    def test_huge_integer_is_rejected_before_decimal_serialization(self):
        graph = self.fresh()
        graph["nodes"][0]["config"]["huge"] = 1 << (MAX_GRAPH_BYTES * 3 + 1)
        with self.assertRaisesRegex(GraphError, "integer"):
            validate(graph)

    def test_valid_json_scalar_types_remain_accepted(self):
        graph = self.fresh()
        graph["nodes"][0]["config"]["metadata"] = {
            "null": None,
            "boolean": True,
            "integer": 7,
            "float": 0.125,
            "unicode": "аквариум 🐠",
            "array": [False, 0, "ok"],
        }
        self.assertEqual(len(validate(graph)[0]), 6)

    def test_huge_integer_node_kind_stays_inside_graph_error_boundary(self):
        graph = self.fresh()
        # Below the preflight bit-length cap but above Python's default
        # decimal-conversion digit limit.
        graph["nodes"][0]["kind"] = 1 << 20_000
        with self.assertRaisesRegex(GraphError, "integer exceeds byte budget"):
            validate(graph)

    def test_schema_mismatch_still_uses_graph_error(self):
        graph = self.fresh()
        graph["schema"] = SCHEMA + "/unexpected"
        with self.assertRaises(GraphError):
            validate(graph)


if __name__ == "__main__":
    unittest.main()
