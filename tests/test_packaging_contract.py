"""Offline packaging contract checks for Mojum distribution metadata and wheel contents."""
from __future__ import annotations

import importlib
import importlib.util
import sys
import tempfile
import tomllib
import unittest
import zipfile
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class PackagingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        cls.project = cls.config["project"]

    def test_runtime_has_no_mandatory_third_party_dependencies(self):
        self.assertGreaterEqual(
            tuple(map(int, self.project["requires-python"].lstrip(">=").split("."))),
            (3, 11),
        )
        self.assertEqual(self.project.get("dependencies", []), [])

    def test_every_console_entry_point_resolves_to_a_callable(self):
        scripts = self.project["scripts"]
        self.assertTrue(scripts)
        for command, target in scripts.items():
            with self.subTest(command=command, target=target):
                module_name, separator, attribute = target.partition(":")
                self.assertEqual(separator, ":")
                value = importlib.import_module(module_name)
                for part in attribute.split("."):
                    value = getattr(value, part)
                self.assertTrue(callable(value), f"{command} target is not callable")

    def test_package_data_globs_match_existing_files(self):
        patterns = self.config["tool"]["setuptools"]["package-data"]["mojum"]
        self.assertTrue(patterns)
        package_root = SRC / "mojum"
        shipped = [
            p.relative_to(package_root).as_posix()
            for p in package_root.rglob("*")
            if p.is_file()
        ]
        for pattern in patterns:
            self.assertTrue(any(fnmatchcase(p, pattern) for p in shipped), pattern)

    def test_default_graph_resource_is_present(self):
        resource = SRC / "mojum" / "data" / "first_loop.json"
        self.assertTrue(resource.is_file())
        self.assertGreater(resource.stat().st_size, 0)

    def test_build_backend_and_package_discovery_use_src_layout(self):
        self.assertEqual(self.config["build-system"]["build-backend"], "setuptools.build_meta")
        setuptools = self.config["tool"]["setuptools"]
        self.assertEqual(setuptools["package-dir"][""], "src")
        self.assertIn("mojum*", setuptools["packages"]["find"]["include"])

    def test_offline_wheel_contains_runtime_and_required_data_only(self):
        # Use the already-installed PEP 517 backend directly. No pip resolver,
        # build isolation, package index, network, or extra dependency is used.
        # Broad stdlib-only repository matrices do not provision build tooling.
        # The dedicated packaging workflow installs setuptools and runs this check.
        if importlib.util.find_spec("setuptools") is None:
            self.skipTest("setuptools is absent; dedicated packaging matrix installs it")
        from setuptools import build_meta

        with tempfile.TemporaryDirectory(prefix="mojum-wheel-") as directory:
            wheel_name = build_meta.build_wheel(directory)
            wheel_path = Path(directory) / wheel_name
            self.assertTrue(wheel_path.is_file(), "backend did not create a wheel")
            with zipfile.ZipFile(wheel_path) as archive:
                names = archive.namelist()

            self.assertIn("mojum/data/first_loop.json", names)
            runtime_modules = [
                name for name in names
                if name.startswith("mojum/") and name.endswith(".py")
            ]
            self.assertTrue(runtime_modules, "wheel contains no Python runtime modules")

            forbidden_parts = {
                "tests", "tools", "docs", ".git", ".github", "__pycache__",
                "private", "journals", "secrets",
            }
            forbidden_suffixes = (".env", ".pem", ".key", ".sqlite", ".sqlite3", ".db")
            for name in names:
                parts = Path(name).parts
                self.assertFalse(set(parts) & forbidden_parts, f"forbidden path packaged: {name}")
                self.assertFalse(
                    any(part.lower().endswith(forbidden_suffixes) for part in parts),
                    f"forbidden file packaged: {name}",
                )
                self.assertFalse(
                    any(part.startswith(".env") for part in parts),
                    f"environment file packaged: {name}",
                )
                self.assertFalse(
                    any(part.endswith(".dist-info") for part in parts[1:-1]),
                    f"unexpected nested metadata path: {name}",
                )


if __name__ == "__main__":
    unittest.main()
