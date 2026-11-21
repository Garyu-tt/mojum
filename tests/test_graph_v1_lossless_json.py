"""Executable Graph v1 must reject non-JSON Python values before hashing."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from mojum.graph import GraphError, load_graph, validate
from mojum.sim import Simulation, digest


FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "examples" / "first_loop.json")
    .read_text(encoding="utf-8")
)


class GraphV1LosslessJsonTests(unittest.TestCase):
    def test_nested_noncanonical_python_values_fail_with_graph_error(self):
        values = (
            {"not_json": {1, 2}},
            {"tuple_changes_wire_type": ("a", "b")},
            {"numeric_key_changes_wire_type": {1: "one"}},
            {"colliding_keys": {1: "numeric", "1": "string"}},
            {"nonfinite": float("nan")},
            {"surrogate": "\ud800"},
        )
        for field in ("config", "notes"):
            for value in values:
                with self.subTest(field=field, value=repr(value)):
                    graph = copy.deepcopy(FIXTURE)
                    graph["nodes"][0][field]["probe"] = value
                    original = copy.deepcopy(graph)
                    with self.assertRaisesRegex(GraphError, "lossless UTF-8 JSON"):
                        validate(graph)
                    with self.assertRaises(GraphError):
                        Simulation(graph)
                    self.assertEqual(set(graph), set(original))

    def test_recursive_config_fails_with_graph_error(self):
        graph = copy.deepcopy(FIXTURE)
        config = graph["nodes"][0]["config"]
        config["self"] = config
        with self.assertRaisesRegex(GraphError, "lossless UTF-8 JSON"):
            validate(graph)
        with self.assertRaises(GraphError):
            Simulation(graph)

    def test_nonfinite_literal_from_json_file_fails_before_simulation(self):
        graph = copy.deepcopy(FIXTURE)
        graph["nodes"][0]["config"]["nan"] = float("nan")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "graph.json"
            path.write_text(json.dumps(graph), encoding="utf-8")
            loaded = load_graph(path)
            with self.assertRaisesRegex(GraphError, "lossless UTF-8 JSON"):
                validate(loaded)
            with self.assertRaises(GraphError):
                Simulation(loaded)

    def test_valid_utf8_json_keeps_graph_digest_and_execution(self):
        graph = copy.deepcopy(FIXTURE)
        graph["nodes"][0]["config"]["extra"] = {
            "emoji": "кот 🐈", "values": [1, True, None, {"ratio": 0.25}],
        }
        graph["nodes"][0]["notes"]["purpose"] = "Наблюдение за комнатой"
        expected = digest(graph)
        nodes, incoming, order = validate(graph)
        self.assertEqual(len(nodes), len(order))
        self.assertEqual(len(incoming), len(nodes))
        sim = Simulation(graph)
        self.assertEqual(sim.graph_hash, expected)
        self.assertIn("episode", sim.step())
        self.assertEqual(digest(graph), expected)


if __name__ == "__main__":
    unittest.main()
