"""Graph v1 must fail closed before canonical UTF-8 hashing or SQLite birth."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mojum.graph import GraphError, load_graph, validate
from mojum.sim import Simulation, digest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class GraphV1CanonicalJsonTests(unittest.TestCase):
    def test_surrogate_in_nonidentity_fields_is_graph_error(self):
        # JSON permits escaped lone surrogates but UTF-8 does not. The root
        # id/revision checks already reject them; arbitrary notes and config
        # must not escape the same gate and crash during SHA-256 hashing.
        for location in ("purpose", "notes", "config", "nested"):
            with self.subTest(location=location):
                graph = copy.deepcopy(FIXTURE)
                if location == "purpose":
                    graph["nodes"][0]["notes"]["purpose"] = "\ud800"
                elif location == "notes":
                    graph["nodes"][0]["notes"]["comment"] = "bad\udfff"
                elif location == "config":
                    graph["nodes"][0]["config"]["description"] = "\ud800"
                else:
                    graph["nodes"][0]["notes"]["nested"] = ["ok", {"bad": "\udfff"}]
                with self.assertRaisesRegex(GraphError, "noncanonical UTF-8 JSON"):
                    validate(graph)
                with self.assertRaisesRegex(GraphError, "noncanonical UTF-8 JSON"):
                    Simulation(graph)

    def test_non_json_config_and_notes_are_rejected_before_hashing(self):
        for value in (float("nan"), float("inf"), {1, 2}, b"bytes"):
            with self.subTest(value=repr(value)):
                graph = copy.deepcopy(FIXTURE)
                graph["nodes"][0]["notes"]["unexpected"] = value
                with self.assertRaisesRegex(GraphError, "noncanonical UTF-8 JSON"):
                    validate(graph)
                with self.assertRaises(GraphError):
                    Simulation(graph)
        graph = copy.deepcopy(FIXTURE)
        cycle = []
        cycle.append(cycle)
        graph["nodes"][0]["notes"]["cycle"] = cycle
        with self.assertRaisesRegex(GraphError, "noncanonical UTF-8 JSON"):
            validate(graph)

    def test_escaped_surrogate_graph_file_refuses_before_sqlite_birth(self):
        graph = copy.deepcopy(FIXTURE)
        graph["nodes"][0]["notes"]["purpose"] = "\ud800"
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "bad-graph.json"
            db = Path(temp) / "must-not-exist.sqlite"
            # ASCII JSON encoding deliberately leaves the illegal scalar as
            # a valid JSON escape. The decoder should accept the text, then
            # the canonical UTF-8 contract must reject its payload.
            source.write_text(json.dumps(graph), encoding="utf-8")
            decoded = load_graph(source)
            with self.assertRaisesRegex(GraphError, "noncanonical UTF-8 JSON"):
                validate(decoded)
            run = subprocess.run(
                [sys.executable, "-m", "mojum.aquarium", "--graph", str(source),
                 "--db", str(db), "--ticks", "1"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertIn("noncanonical UTF-8 JSON", run.stderr)
            self.assertFalse(db.exists())
            self.assertEqual(run.stdout, "")

    def test_valid_multilingual_utf8_notes_keep_existing_execution(self):
        graph = copy.deepcopy(FIXTURE)
        graph["nodes"][0]["notes"]["purpose"] = "Данте исследует мир 🐈"
        graph["nodes"][0]["config"]["description"] = "観察"
        expected = digest(graph)
        nodes, _, order = validate(graph)
        self.assertEqual(len(nodes), len(order))
        sim = Simulation(graph)
        self.assertEqual(sim.graph_hash, expected)
        self.assertIn("episode", sim.step())
        self.assertEqual(sim.tick, 1)


if __name__ == "__main__":
    unittest.main()
