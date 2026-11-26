"""Packaging regression: fixture defaults survive installation outside source cwd."""
import json
import os
from importlib.resources import files
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

from mojum.genesis import validate_seed
from mojum.graph import validate

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"


class InstallableAquariumTests(unittest.TestCase):
    def test_package_resources_are_valid_and_match_current_fixtures(self):
        for name in ("first_loop.json", "genesis_seed.json"):
            with self.subTest(name=name):
                packaged = json.loads(files("mojum").joinpath("data", name).read_text(encoding="utf-8"))
                original = json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))
                self.assertEqual(packaged, original)
        validate(json.loads(files("mojum").joinpath("data", "first_loop.json").read_text(encoding="utf-8")))
        validate_seed(json.loads(files("mojum").joinpath("data", "genesis_seed.json").read_text(encoding="utf-8")))

    def test_package_metadata_and_console_scripts_are_declared(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["name"], "mojum")
        self.assertEqual(metadata["project"]["version"], "0.1.2")
        self.assertEqual(metadata["project"]["dependencies"], [])
        self.assertEqual(metadata["project"]["requires-python"], ">=3.11")
        self.assertEqual(metadata["project"]["scripts"]["mojum-aquarium"], "mojum.aquarium:main")
        self.assertEqual(metadata["tool"]["setuptools"]["package-data"]["mojum"],
                         ["data/*.json", "data/*.html"])

    def test_cli_defaults_start_and_resume_outside_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "dante-fixture.sqlite3"
            env = os.environ.copy()
            # PYTHONPATH=src from CI is relative to cwd and must not silently
            # make this work only from a checkout.
            env["PYTHONPATH"] = str(SOURCE) + os.pathsep + env.get("PYTHONPATH", "")
            command = [sys.executable, "-m", "mojum.aquarium",
                       "--db", str(db), "--neural-mode", "disabled"]
            first = json.loads(subprocess.check_output(
                [*command, "--ticks", "2"], cwd=folder, env=env, text=True, timeout=60))
            second = json.loads(subprocess.check_output(
                [*command, "--ticks", "1"], cwd=folder, env=env, text=True, timeout=60))
            self.assertEqual(first["tick"], 2)
            self.assertEqual(first["stored_events"], 3)
            self.assertEqual(second["tick"], 3)
            self.assertEqual(second["stored_events"], 4)
            self.assertEqual(first["status"], "SYNTHETIC_PLACEHOLDER_NOT_NEURAL_OR_MUJOCO")
            self.assertTrue(db.is_file())

    def test_cli_uses_selected_genome_identity_on_start_and_resume(self):
        seed = json.loads(files("mojum").joinpath("data", "genesis_seed.json").read_text(encoding="utf-8"))
        seed["genome_id"] = "portable-fixture-\u661f"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            genome = root / "custom-seed.json"
            genome.write_text(json.dumps(seed), encoding="utf-8")
            # CP1251 cannot represent the selected genome ID in redirected output.
            env = {**os.environ, "PYTHONPATH": str(SOURCE), "PYTHONIOENCODING": "cp1251"}
            command = [sys.executable, "-m", "mojum.aquarium",
                       "--db", str(root / "custom.sqlite3"), "--neural-mode", "disabled"]

            def run(ticks):
                return json.loads(subprocess.check_output(
                    [*command, "--genome", str(genome), "--ticks", str(ticks)],
                    cwd=root, env=env, encoding="cp1251", timeout=60))

            first = run(2)
            resumed = run(1)
            self.assertEqual(first["agent_id"], seed["genome_id"])
            self.assertEqual((first["tick"], first["stored_events"]), (2, 3))
            self.assertEqual(resumed["agent_id"], first["agent_id"])
            self.assertEqual((resumed["tick"], resumed["stored_events"]), (3, 4))

            default_genome = subprocess.run(
                [*command, "--ticks", "1"], cwd=root, env=env, text=True,
                capture_output=True, timeout=60)
            self.assertNotEqual(default_genome.returncode, 0)
            self.assertIn("database schema or agent owner does not match", default_genome.stderr)
            self.assertEqual(run(0), resumed)


if __name__ == "__main__":
    unittest.main()
