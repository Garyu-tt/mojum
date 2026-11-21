"""Canonical executable graph-v1 requires a stable root identity and revision."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mojum.graph import GraphError, validate
from mojum.sim import Simulation, digest


FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "examples" / "first_loop.json").read_text(encoding="utf-8")
)


class GraphV1RootMetadataTests(unittest.TestCase):
    def test_missing_root_identity_or_revision_is_not_silently_accepted(self):
        for field in ("id", "revision"):
            with self.subTest(field=field):
                graph = copy.deepcopy(FIXTURE)
                del graph[field]
                with self.assertRaisesRegex(GraphError, "exact root fields"):
                    validate(graph)
                with self.assertRaises(GraphError):
                    Simulation(graph)

    def test_root_metadata_rejects_wrong_type_empty_overlong_and_surrogate(self):
        invalid = (None, False, True, 1, 1.5, [], {}, "", " \t", "x" * 257, "\ud800")
        for field in ("id", "revision"):
            for value in invalid:
                with self.subTest(field=field, value=repr(value)):
                    graph = copy.deepcopy(FIXTURE)
                    graph[field] = value
                    with self.assertRaisesRegex(GraphError, field):
                        validate(graph)
                    with self.assertRaises(GraphError):
                        Simulation(graph)

    def test_valid_unicode_graph_preserves_execution_and_hash(self):
        graph = copy.deepcopy(FIXTURE)
        graph["id"] = "aquarium-аквариум"
        graph["revision"] = "2.1-🎨"
        before = digest(graph)
        nodes, incoming, order = validate(graph)
        self.assertEqual(len(nodes), len(order))
        self.assertEqual(len(incoming), len(nodes))
        sim = Simulation(graph)
        self.assertEqual(sim.graph_hash, before)
        self.assertEqual(sim.tick, 0)
        self.assertIn("episode", sim.step())
        self.assertEqual(digest(graph), before)
        self.assertEqual(FIXTURE["revision"], "0.1.0-fixture")

    def test_missing_root_metadata_cli_fails_before_journal_creation(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            graph_path = Path(temporary) / "invalid-graph.json"
            db_path = Path(temporary) / "must-not-exist.sqlite"
            for field in ("id", "revision"):
                graph = copy.deepcopy(FIXTURE)
                del graph[field]
                graph_path.write_text(json.dumps(graph), encoding="utf-8")
                run = subprocess.run(
                    [sys.executable, "-m", "mojum.aquarium", "--graph", str(graph_path),
                     "--db", str(db_path), "--ticks", "1"],
                    cwd=root, capture_output=True, text=True, check=False,
                )
                with self.subTest(field=field):
                    self.assertEqual(run.returncode, 2, run.stderr)
                    self.assertIn("root fields", run.stderr)
                    self.assertFalse(db_path.exists())
                    self.assertEqual(run.stdout, "")

    def test_unknown_top_level_field_still_refused(self):
        graph = copy.deepcopy(FIXTURE)
        graph["permissions"] = {"execute": True}
        with self.assertRaisesRegex(GraphError, "root fields"):
            validate(graph)


if __name__ == "__main__":
    unittest.main()
