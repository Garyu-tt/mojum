"""Regression tests for minimal graph contract; stdlib only."""
import unittest

from mojum_contract import GraphContractError, VERSION, validate_graph


def fixture():
    return {
        "schema": VERSION,
        "nodes": [
            {"id": "sensor", "visibility": "PRIVATE", "inputs": {}, "outputs": {"sample": "float"}},
            {"id": "sink", "visibility": "RESTRICTED", "inputs": {"value": "float"}, "outputs": {}},
        ],
        "edges": [{"source": "sensor", "source_port": "sample", "target": "sink", "target_port": "value"}],
    }


class GraphContractTests(unittest.TestCase):
    def test_valid_graph(self):
        self.assertIsNone(validate_graph(fixture()))

    def test_duplicate_node(self):
        graph = fixture()
        graph["nodes"].append(dict(graph["nodes"][0]))
        with self.assertRaises(GraphContractError):
            validate_graph(graph)

    def test_type_mismatch(self):
        graph = fixture()
        graph["nodes"][1]["inputs"]["value"] = "string"
        with self.assertRaises(GraphContractError):
            validate_graph(graph)

    def test_dangling_edge(self):
        graph = fixture()
        graph["edges"][0]["target"] = "missing"
        with self.assertRaises(GraphContractError):
            validate_graph(graph)

    def test_visibility_required(self):
        graph = fixture()
        del graph["nodes"][0]["visibility"]
        with self.assertRaises(GraphContractError):
            validate_graph(graph)

    def test_unsupported_schema(self):
        graph = fixture()
        graph["schema"] = "unknown"
        with self.assertRaises(GraphContractError):
            validate_graph(graph)


if __name__ == "__main__":
    unittest.main()
