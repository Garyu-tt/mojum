"""Malformed node kinds must yield GraphError, not an unhashable TypeError.

Stacked regression over PR #29, without modifying the peer-3 work branch.
"""
import copy
import json
import unittest
from pathlib import Path

from mojum.graph import GraphError, validate

FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "first_loop.json")
    .read_text(encoding="utf-8")
)


class InvalidNodeKindTests(unittest.TestCase):
    def test_nonstring_kind_is_rejected_at_every_node(self):
        for index in range(len(FIXTURE["nodes"])):
            for invalid in ([], {}, None, False, True, 7, 1.5, (), (1,), {"x": "y"}):
                with self.subTest(node=index, kind=invalid):
                    graph = copy.deepcopy(FIXTURE)
                    graph["nodes"][index]["kind"] = invalid
                    with self.assertRaisesRegex(GraphError, "Unsupported node kind"):
                        validate(graph)

    def test_unknown_string_kind_is_rejected(self):
        graph = copy.deepcopy(FIXTURE)
        graph["nodes"][0]["kind"] = "unsupported.fixture-kind"
        with self.assertRaisesRegex(GraphError, "Unsupported node kind"):
            validate(graph)

    def test_valid_canonical_graph_remains_executable(self):
        by_id, incoming, order = validate(copy.deepcopy(FIXTURE))
        self.assertEqual(len(by_id), len(FIXTURE["nodes"]))
        self.assertEqual(set(order), set(by_id))
        self.assertEqual(len(order), len(set(order)))
        self.assertEqual(set(incoming), set(incoming).intersection(by_id))


if __name__ == "__main__":
    unittest.main()
