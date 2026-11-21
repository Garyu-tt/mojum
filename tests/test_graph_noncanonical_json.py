"""Fail-closed tests for noncanonical Graph IR numeric/UTF-8 values.

All fixtures are synthetic and temporary; no PRIVATE journals are opened.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.graph import GraphError, load_graph, validate
from mojum.sim import Simulation, digest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "examples" / "first_loop.json").read_text(encoding="utf-8"))


class NoncanonicalGraphTests(unittest.TestCase):
    def test_programmatic_nonfinite_floats_are_typed_errors(self):
        for number in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=repr(number)):
                graph = copy.deepcopy(FIXTURE)
                graph["nodes"][0]["config"]["bpm"] = number
                with self.assertRaisesRegex(GraphError, "noncanonical JSON"):
                    validate(graph)
                with self.assertRaises(GraphError):
                    Simulation(graph)

    def test_programmatic_invalid_utf8_and_nonjson_values_are_typed_errors(self):
        for field, value in (
            ("purpose", "\ud800"),  # lone surrogate is not UTF-8
            ("payload", {"not", "JSON"}),  # a Python set cannot be hashed as JSON
        ):
            with self.subTest(field=field):
                graph = copy.deepcopy(FIXTURE)
                if field == "purpose":
                    graph["nodes"][0]["notes"][field] = value
                else:
                    graph["nodes"][0]["config"][field] = value
                with self.assertRaisesRegex(GraphError, "noncanonical JSON"):
                    validate(graph)
                with self.assertRaises(GraphError):
                    Simulation(graph)

    def test_json_extensions_nan_and_infinity_refused_by_loader(self):
        raw = json.dumps(FIXTURE)
        self.assertIn('"bpm": 60', raw)
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "nonfinite.json"
            for token in ("NaN", "Infinity", "-Infinity"):
                with self.subTest(token=token):
                    path.write_text(raw.replace('"bpm": 60', '"bpm": ' + token),
                                    encoding="utf-8")
                    with self.assertRaisesRegex(GraphError, "Noncanonical graph JSON"):
                        load_graph(path)

    def test_json_float_overflow_refused_before_simulation(self):
        raw = json.dumps(FIXTURE).replace('"bpm": 60', '"bpm": 1e999')
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "overflow.json"
            path.write_text(raw, encoding="utf-8")
            graph = load_graph(path)
            with self.assertRaisesRegex(GraphError, "noncanonical JSON"):
                validate(graph)

    def test_cli_rejects_nan_without_creating_sqlite_journal(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            graph_path = root / "invalid.json"
            journal_path = root / "must-not-exist.sqlite3"
            graph_path.write_text(
                json.dumps(FIXTURE).replace('"bpm": 60', '"bpm": NaN'),
                encoding="utf-8",
            )
            env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
            result = subprocess.run(
                [sys.executable, "-m", "mojum.aquarium", "--graph", str(graph_path),
                 "--db", str(journal_path), "--ticks", "1"],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("Noncanonical graph JSON", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertFalse(journal_path.exists())

    def test_canonical_graph_digests_and_executes_unchanged(self):
        graph = copy.deepcopy(FIXTURE)
        self.assertEqual(len(validate(graph)[0]), 6)
        self.assertEqual(Simulation(graph).graph_hash, digest(FIXTURE))
        simulation = Simulation(graph)
        self.assertIn("episode", simulation.step())
        self.assertEqual(simulation.snapshot()["state"]["tick"], 1)


if __name__ == "__main__":
    unittest.main()
