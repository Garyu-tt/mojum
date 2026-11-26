"""Installed fixture runner reports an unsupported executor config as input error."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "src" / "mojum" / "data" / "first_loop.json"


class FixtureCliRuntimeTests(unittest.TestCase):
    def test_invalid_executor_configuration_is_a_usage_error(self):
        cases = (
            ("genesis.seed", "bpm", 12, "Genome bpm must be an integer in [30,180]"),
            ("world.room", "light", "unknown", "Invalid world.room light fixture"),
        )
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom graph.json"
            for kind, field, value, diagnostic in cases:
                with self.subTest(kind=kind):
                    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
                    node = next(node for node in graph["nodes"] if node["kind"] == kind)
                    node["config"][field] = value
                    path.write_text(json.dumps(graph), encoding="utf-8")
                    result = subprocess.run(
                        [sys.executable, "-m", "mojum", str(path), "--ticks", "1"],
                        cwd=directory, env=env, capture_output=True, text=True, timeout=20,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn(diagnostic, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
