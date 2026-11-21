"""Graph JSON loading rejects interpreter-dependent nesting failures."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.graph import GraphError, load_graph


ROOT = Path(__file__).resolve().parents[1]


class GraphJsonDepthTests(unittest.TestCase):
    def test_deep_json_fails_with_typed_graph_error(self):
        raw = "[" * 257 + "0" + "]" * 257
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "deep.json"
            path.write_text(raw, encoding="utf-8")
            with self.assertRaisesRegex(GraphError, "nesting depth exceeded"):
                load_graph(path)

    def test_brackets_inside_json_strings_do_not_count_as_nesting(self):
        graph = json.loads(
            (ROOT / "examples" / "first_loop.json").read_text(encoding="utf-8")
        )
        graph["nodes"][0]["notes"]["purpose"] = "[" * 512 + "]" * 512
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "brackets-in-string.json"
            path.write_text(json.dumps(graph), encoding="utf-8")
            self.assertEqual(load_graph(path), graph)

    def test_aquarium_cli_rejects_deep_graph_before_creating_journal(self):
        raw = "[" * 257 + "0" + "]" * 257
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            graph = root / "deep.json"
            journal = root / "aquarium.sqlite3"
            graph.write_text(raw, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "mojum.aquarium", "--graph", str(graph),
                 "--db", str(journal), "--ticks", "1"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("nesting depth exceeded", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertFalse(journal.exists())


if __name__ == "__main__":
    unittest.main()
