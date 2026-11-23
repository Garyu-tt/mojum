"""Regression coverage for malformed Universal Graph v1 node kinds."""

import copy
import unittest

from mojum.graph import GraphError, validate


def _minimal_graph():
    """Return the smallest valid graph, detached for each mutation."""
    return {
        "schema": "mechanicus.universal-graph/1",
        "id": "graph-kind-regression",
        "revision": "1",
        "nodes": [
            {
                "id": "seed",
                "kind": "genesis.seed",
                "config": {},
                "notes": {"purpose": "synthetic regression fixture"},
            }
        ],
        "edges": [],
    }


class GraphNodeKindRegressionTests(unittest.TestCase):
    def test_valid_string_kind_remains_accepted(self):
        by_id, incoming, order = validate(_minimal_graph())

        self.assertEqual(set(by_id), {"seed"})
        self.assertEqual(dict(incoming["seed"]), {})
        self.assertEqual(order, ["seed"])

    def test_non_string_and_unhashable_kinds_raise_graph_error(self):
        # Lists and dictionaries are unhashable. The validator must reject
        # them through its public contract instead of leaking TypeError from
        # the PORTS dictionary membership check.
        for malformed_kind in ([], {}, None, False):
            with self.subTest(kind=malformed_kind):
                graph = copy.deepcopy(_minimal_graph())
                graph["nodes"][0]["kind"] = malformed_kind

                with self.assertRaises(GraphError):
                    validate(graph)

    def test_unknown_string_kind_raises_graph_error(self):
        graph = _minimal_graph()
        graph["nodes"][0]["kind"] = "not.registered"

        with self.assertRaises(GraphError):
            validate(graph)


if __name__ == "__main__":
    unittest.main()
