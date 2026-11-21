"""Executable graph-v1 must reject non-JSON nested data before hashing or SQLite."""

import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mojum.graph import GraphError, load_graph, validate
from mojum.sim import Simulation, digest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "examples" / "first_loop.json").read_text(encoding="utf-8"))


class GraphV1LosslessWireTests(unittest.TestCase):
    def test_nested_noncanonical_values_fail_closed_as_graph_error(self):
        cases = {
            "unpaired_surrogate_purpose": ("notes", "purpose", "\ud800"),
            "unpaired_surrogate_config": ("config", "extra", "\udfff"),
            "nan": ("config", "extra", math.nan),
            "infinity": ("config", "extra", math.inf),
            "tuple_coerces_to_list": ("notes", "extra", (1, 2)),
            "set_is_not_json": ("notes", "extra", {1, 2}),
            "integer_dict_key_coerces_to_string": ("notes", "extra", {1: "value"}),
            "mixed_keys_not_sortable": ("notes", "extra", {"1": "a", 1: "b"}),
        }
        for name, (field, key, value) in cases.items():
            with self.subTest(case=name):
                graph = copy.deepcopy(FIXTURE)
                graph["nodes"][0][field][key] = value
                with self.assertRaisesRegex(GraphError, "lossless UTF-8 JSON"):
                    validate(graph)
                with self.assertRaises(GraphError):
                    Simulation(graph)

    def test_nested_cycles_fail_closed_without_recursion_error(self):
        graph = copy.deepcopy(FIXTURE)
        loop = {}
        loop["self"] = loop
        graph["nodes"][0]["notes"]["loop"] = loop
        with self.assertRaisesRegex(GraphError, "lossless UTF-8 JSON"):
            validate(graph)

    def test_valid_nested_unicode_remains_executable_and_stable(self):
        graph = copy.deepcopy(FIXTURE)
        graph["nodes"][0]["notes"]["localization"] = {
            "ru": "аквариум", "emoji": ["🐠", "🎨"], "nest": {"ok": True}
        }
        expected = digest(graph)
        nodes, _, order = validate(graph)
        self.assertEqual(len(nodes), len(order))
        sim = Simulation(graph)
        self.assertEqual(sim.graph_hash, expected)
        self.assertEqual(len(sim.step()), len(FIXTURE["nodes"]))
        self.assertEqual(graph["nodes"][0]["notes"]["localization"]["ru"], "аквариум")

    def test_loader_rejects_nonfinite_json_numbers(self):
        raw = json.dumps(FIXTURE)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.json"
            for token in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999"):
                with self.subTest(token=token):
                    path.write_text(raw.replace('"bpm": 60', f'"bpm": {token}'), encoding="utf-8")
                    with self.assertRaisesRegex(GraphError, "graph JSON numeric constant|finite float"):
                        load_graph(path)

    def test_int_over_parser_limit_does_not_create_private_journal(self):
        raw = json.dumps(FIXTURE).replace('"bpm": 60', '"bpm": ' + "1" * 641)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "invalid.json"
            db = root / "should-not-exist.sqlite3"
            path.write_text(raw, encoding="utf-8")
            process = subprocess.run(
                [sys.executable, "-X", "int_max_str_digits=640", "-m", "mojum.aquarium",
                 "--graph", str(path), "--db", str(db), "--ticks", "1"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(process.returncode, 2, process.stderr)
            self.assertIn("Malformed graph JSON", process.stderr)
            self.assertNotIn("Traceback", process.stderr)
            self.assertFalse(db.exists())

    def test_ascii_escaped_surrogate_cli_does_not_create_private_journal(self):
        # JSON can spell a lone surrogate with ASCII escapes, but the decoded
        # Python string cannot be losslessly encoded in the canonical UTF-8 wire.
        graph = copy.deepcopy(FIXTURE)
        graph["nodes"][0]["notes"]["purpose"] = "\ud800"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "invalid.json"
            db = root / "should-not-exist.sqlite3"
            path.write_text(json.dumps(graph, ensure_ascii=True), encoding="utf-8")
            process = subprocess.run(
                [sys.executable, "-m", "mojum.aquarium",
                 "--graph", str(path), "--db", str(db), "--ticks", "1"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(process.returncode, 2, process.stderr)
            self.assertIn("lossless UTF-8 JSON", process.stderr)
            self.assertFalse(db.exists())
            self.assertEqual(process.stdout, "")


if __name__ == "__main__":
    unittest.main()
