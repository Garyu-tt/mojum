"""Installed-viewer resource parity and actual offline CLI publication."""
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from mojum.trace_viewer import SCHEMA, export_viewer


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "examples" / "aquarium_trace_viewer.html"


class TraceViewerPackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
                    "PYTHONIOENCODING": "ascii:strict"}

    def command(self, output):
        return [sys.executable, "-m", "mojum.trace_viewer", "--output", str(output)]

    def run_cli(self, output, **kwargs):
        return subprocess.run(self.command(output), cwd=self.root,
                              env=self.env, capture_output=True, timeout=20, **kwargs)

    def assert_receipt(self, receipt, output):
        self.assertEqual(output.read_bytes(), CANONICAL.read_bytes())
        self.assertEqual(receipt, {
            "schema": SCHEMA,
            "bytes": len(output.read_bytes()),
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        })

    def test_packaged_copy_matches_canonical_viewer_bytes(self):
        # examples/ is the canonical source. Viewer edits must refresh the
        # package copy so installed users receive the tested application.
        self.assertEqual(files("mojum").joinpath("data", "aquarium_trace_viewer.html").read_bytes(),
                         CANONICAL.read_bytes())

    def test_export_writes_complete_resource_and_metadata(self):
        output = self.root / "viewer.html"
        self.assert_receipt(export_viewer(output), output)
        self.assertEqual({path.name for path in self.root.iterdir()}, {output.name})

    def test_cli_runs_outside_checkout_with_unicode_path_and_ascii_stdout(self):
        output = self.root / "viewer-\u661f.html"
        result = self.run_cli(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assert_receipt(json.loads(result.stdout), output)
        self.assertNotIn(str(self.root).encode(), result.stdout)

    def test_existing_file_or_directory_is_preserved_without_success_receipt(self):
        file = self.root / "existing.html"
        file.write_bytes(b"existing user content")
        directory = self.root / "directory.html"
        directory.mkdir()
        for target in (file, directory):
            with self.subTest(target=target.name):
                result = self.run_cli(target)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"output already exists", result.stderr)
        self.assertEqual(file.read_bytes(), b"existing user content")
        self.assertTrue(directory.is_dir())
        self.assertEqual({path.name for path in self.root.iterdir()}, {file.name, directory.name})

    def test_existing_and_dangling_symlinks_are_preserved(self):
        existing = self.root / "user.txt"
        existing.write_bytes(b"keep user data")
        missing = self.root / "missing.txt"
        for index, target in enumerate((existing, missing)):
            link = self.root / f"viewer-{index}.html"
            try:
                link.symlink_to(target)
            except (NotImplementedError, OSError):
                self.skipTest("symlink creation is unavailable")
            result = self.run_cli(link)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.readlink(), target)
        self.assertEqual(existing.read_bytes(), b"keep user data")
        self.assertFalse(missing.exists())

    def test_missing_parent_fails_without_creating_a_directory(self):
        output = self.root / "missing" / "viewer.html"
        result = self.run_cli(output)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_competing_processes_publish_only_one_complete_file(self):
        output = self.root / "viewer.html"
        children = [subprocess.Popen(self.command(output), cwd=self.root, env=self.env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    for _ in range(2)]
        try:
            results = [child.communicate(timeout=20) for child in children]
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
        self.assertEqual(sorted(child.returncode for child in children), [0, 2])
        for child, (stdout, stderr) in zip(children, results):
            if child.returncode == 0:
                self.assertEqual(stderr, b"")
                self.assert_receipt(json.loads(stdout), output)
            else:
                self.assertEqual(stdout, b"")
        self.assertEqual({path.name for path in self.root.iterdir()}, {output.name})

    def test_resource_can_be_loaded_from_a_zip_package(self):
        # importlib.resources must not assume the installed asset is a Path.
        package = self.root / "package.zip"
        with zipfile.ZipFile(package, "w") as archive:
            for name in ("__init__.py", "trace_viewer.py", "data/aquarium_trace_viewer.html"):
                archive.write(ROOT / "src" / "mojum" / name, "mojum/" + name)
        self.env["PYTHONPATH"] = str(package)
        output = self.root / "viewer.html"
        result = self.run_cli(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_receipt(json.loads(result.stdout), output)


if __name__ == "__main__":
    unittest.main()
