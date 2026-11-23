"""Regression: malformed node kinds fail closed alongside typed-edge validation."""

import copy
import json
import unittest
from pathlib import Path

from mojum.graph import GraphError, validate


FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "first_loop.json").read_text(encoding="utf-8")
)


class MalformedNodeKindTests(unittest.TestCase):
    def test_nonstring_kinds_raise_graph_error_for_every_node(self):
        invalid_kinds = ([], {}, None, False, True, 0, 3.14, ["genesis.seed"], {"kind": "genesis.seed"})
        for node_index in range(len(FIXTURE["nodes"])):
            for kind in invalid_kinds:
                with self.subTest(node_index=node_index, kind=kind):
                    graph = copy.deepcopy(FIXTURE)
                    graph["nodes"][node_index]["kind"] = kind
                    with self.assertRaisesRegex(GraphError, "Unsupported node kind"):
                        validate(graph)

    def test_missing_and_unknown_string_kinds_fail_closed(self):
        for kind in ("", "python.exec", "genesis.seed "):
            with self.subTest(kind=kind):
                graph = copy.deepcopy(FIXTURE)
                graph["nodes"][0]["kind"] = kind
                with self.assertRaises(GraphError):
                    validate(graph)
        graph = copy.deepcopy(FIXTURE)
        del graph["nodes"][0]["kind"]
        with self.assertRaises(GraphError):
            validate(graph)

    def test_valid_fixture_keeps_existing_topological_order(self):
        original = copy.deepcopy(FIXTURE)
        nodes, incoming, order = validate(original)
        self.assertEqual(set(nodes), set(order))
        self.assertEqual(len(order), len(FIXTURE["nodes"]))
        self.assertEqual(len(incoming), len(nodes))
        self.assertEqual(original, FIXTURE)


if __name__ == "__main__":
    unittest.main()
