"""Independent static contract checks for Mojum distribution metadata."""
from __future__ import annotations
import importlib, sys, tomllib, unittest
from fnmatch import fnmatch
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path: sys.path.insert(0, str(SRC))
class PackagingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        cls.project = cls.config["project"]
    def test_runtime_has_no_mandatory_third_party_dependencies(self):
        self.assertGreaterEqual(tuple(map(int, self.project["requires-python"].lstrip(">=").split("."))), (3, 11))
        self.assertEqual(self.project.get("dependencies", []), [])
    def test_every_console_entry_point_resolves_to_a_callable(self):
        scripts = self.project["scripts"]
        self.assertTrue(scripts)
        for command, target in scripts.items():
            with self.subTest(command=command, target=target):
                module_name, separator, attribute = target.partition(":")
                self.assertEqual(separator, ":")
                value = importlib.import_module(module_name)
                for part in attribute.split("."): value = getattr(value, part)
                self.assertTrue(callable(value), f"{command} target is not callable")
    def test_package_data_globs_match_existing_files(self):
        patterns = self.config["tool"]["setuptools"]["package-data"]["mojum"]
        self.assertTrue(patterns)
        package_root = SRC / "mojum"
        shipped = [p.relative_to(package_root).as_posix() for p in package_root.rglob("*") if p.is_file()]
        for pattern in patterns: self.assertTrue(any(fnmatch(p, pattern) for p in shipped), pattern)
    def test_default_graph_resource_is_present(self):
        resource = SRC / "mojum" / "data" / "first_loop.json"
        self.assertTrue(resource.is_file())
        self.assertGreater(resource.stat().st_size, 0)
    def test_build_backend_and_package_discovery_use_src_layout(self):
        self.assertEqual(self.config["build-system"]["build-backend"], "setuptools.build_meta")
        setuptools = self.config["tool"]["setuptools"]
        self.assertEqual(setuptools["package-dir"][""], "src")
        self.assertIn("mojum*", setuptools["packages"]["find"]["include"])
if __name__ == "__main__": unittest.main()