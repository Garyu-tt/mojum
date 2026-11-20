"""Regression: invalid graph node kinds fail closed, including unhashables.

Stacked atop PR #29, which independently validates malformed edge endpoints.
"""
import copy
import json
from pathlib import Path
import unittest

from mojum.graph import GraphError, validate


FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "first_loop.json")
    .read_text(encoding="utf-8")
)


class MalformedKindTests(unittest.TestCase):
    def test_unhashable_and_nonstring_node_kinds_raise_graph_error(self):
        bad_kinds = ([], {}, None, False, True, 0, 1.5, ["genesis.seed"], ("genesis.seed",))
        for node_index in range(len(FIXTURE["nodes"])):
            for value in bad_kinds:
                with self.subTest(node_index=node_index, value=value):
                    graph = copy.deepcopy(FIXTURE)
                    graph["nodes"][node_index]["kind"] = value
                    with self.assertRaisesRegex(GraphError, "Unsupported node kind"):
                        validate(graph)

    def test_missing_and_unknown_kinds_reject_without_typeerror(self):
        graph = copy.deepcopy(FIXTURE)
        del graph["nodes"][0]["kind"]
        with self.assertRaises(GraphError):
            validate(graph)
        graph = copy.deepcopy(FIXTURE)
        graph["nodes"][0]["kind"] = "nonexistent.node"
        with self.assertRaises(GraphError):
            validate(graph)

    def test_known_good_graph_retains_topological_order(self):
        by_id, incoming, order = validate(copy.deepcopy(FIXTURE))
        self.assertEqual(set(order), set(by_id))
        self.assertLess(order.index("genome"), order.index("heart"))
        self.assertLess(order.index("heart"), order.index("sense"))
        self.assertIn("genome", incoming["heart"])


if __name__ == "__main__":
    unittest.main()
