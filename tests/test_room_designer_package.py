"""Wheel editor resources and real JavaScript draft -> Aquarium integration."""
from contextlib import closing
import hashlib
from importlib.resources import files
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile

from mojum.room_designer import SCHEMA, export_designer


ROOT = Path(__file__).resolve().parents[1]


def canonical_bundle():
    spec = importlib.util.spec_from_file_location(
        "_mojum_designer_package_source", ROOT / "tools" / "package_room_designer.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("canonical room designer packer is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.standalone_html()


class RoomDesignerPackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
                    "PYTHONIOENCODING": "ascii:strict"}

    def command(self, output):
        return [sys.executable, "-m", "mojum.room_designer", "--output", str(output)]

    def run_cli(self, output):
        return subprocess.run(self.command(output), cwd=self.root, env=self.env,
                              capture_output=True, timeout=20)

    def assert_receipt(self, receipt, output):
        data = output.read_bytes()
        self.assertEqual(data, canonical_bundle())
        self.assertEqual(receipt, {"schema": SCHEMA, "bytes": len(data),
                                   "sha256": hashlib.sha256(data).hexdigest()})

    def test_packaged_bundle_matches_canonical_packer(self):
        data = files("mojum").joinpath("data", "room_designer.html").read_bytes()
        self.assertEqual(data, canonical_bundle())
        self.assertNotRegex(data, rb"<script\b[^>]*\bsrc\s*=")

    def test_export_writes_complete_bundle_and_metadata(self):
        output = self.root / "designer.html"
        self.assert_receipt(export_designer(output), output)
        self.assertEqual({path.name for path in self.root.iterdir()}, {output.name})

    def test_cli_runs_outside_checkout_with_unicode_path_and_ascii_stdout(self):
        output = self.root / "designer-\u661f.html"
        result = self.run_cli(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assert_receipt(json.loads(result.stdout), output)
        self.assertNotIn(str(self.root).encode(), result.stdout)

    def test_existing_file_and_directory_are_preserved(self):
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
            link = self.root / f"designer-{index}.html"
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
        output = self.root / "missing" / "designer.html"
        result = self.run_cli(output)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_competing_processes_publish_only_one_complete_bundle(self):
        output = self.root / "designer.html"
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

    def test_resource_loads_from_a_zip_package(self):
        package = self.root / "package.zip"
        with zipfile.ZipFile(package, "w") as archive:
            for name in ("__init__.py", "room_designer.py", "data/room_designer.html"):
                archive.write(ROOT / "src" / "mojum" / name, "mojum/" + name)
        self.env["PYTHONPATH"] = str(package)
        output = self.root / "designer.html"
        result = self.run_cli(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_receipt(json.loads(result.stdout), output)

    def test_bundled_editor_json_boots_and_cold_resumes_pinned_room(self):
        node = shutil.which("node")
        if node is None:
            if os.environ.get("CI"):
                self.fail("Node is required for the packaged editor integration in CI")
            self.skipTest("Node is unavailable for the optional local editor-core probe")
        output = self.root / "designer.html"
        self.assertEqual(self.run_cli(output).returncode, 0)
        # Execute the actual bundled model script, without a mock replacement
        # or a browser: edit a room and use its production draftJSON function.
        script = r"""
const fs = require('node:fs'), vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
const context = vm.createContext({TextEncoder});
vm.runInContext(scripts[0][1], context, {timeout: 3000});
const core = context.MojumRoomDesignerCore;
let room = core.clone(core.DEFAULT);
room.width = '6.0'; room.height = '5';
room = core.dragStart(room, 1.5, 2.5);
room = core.dragObstacle(room, 0, 3, 1);
process.stdout.write(core.draftJSON(room));
"""
        generated = subprocess.run([node, "-e", script, str(output)], cwd=self.root,
                                   capture_output=True, check=True, timeout=10).stdout
        self.assertIn(b'"width": 6.0', generated)
        room_file = self.root / "authored-room.json"
        room_file.write_bytes(generated)

        def run(module, *arguments):
            return subprocess.run([sys.executable, "-m", module, *map(str, arguments)],
                                  cwd=self.root, env=self.env, capture_output=True, timeout=20)

        checked = run("mojum.room_spec", room_file, "--format", "json")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertTrue(json.loads(checked.stdout)["valid"])
        journal = self.root / "room.sqlite3"
        base = ("--room-file", room_file, "--neural-mode", "disabled")
        first = run("mojum.aquarium", "--db", journal, "--ticks", "2", *base)
        self.assertEqual(first.returncode, 0, first.stderr)
        first_state = json.loads(first.stdout)
        resumed = run("mojum.aquarium", "--db", journal, "--ticks", "1", *base,
                      "--expected-head", first_state["journal_head"])
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        state = json.loads(resumed.stdout)
        self.assertEqual((state["tick"], state["stored_events"]), (3, 4))
        control = run("mojum.aquarium", "--db", self.root / "control.sqlite3",
                      "--ticks", "3", *base)
        self.assertEqual(control.returncode, 0, control.stderr)
        self.assertEqual(state, json.loads(control.stdout))
        with closing(sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True)) as db:
            wire = db.execute("SELECT wire_json FROM events WHERE seq = 1").fetchone()[0]
        self.assertEqual(json.loads(wire)["payload"]["room"], {
            "width": 6.0, "height": 5.0, "start": [1.5, 2.5],
            "obstacles": [[3.0, 1.0, 0.125, 2.0]],
        })

        room_file.write_bytes(generated.replace(b'"width": 6.0', b'"width": 7.0'))
        rejected = run("mojum.aquarium", "--db", journal, "--ticks", "1", *base)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(rejected.stdout, b"")
        room_file.write_bytes(generated)
        reopened = run("mojum.aquarium", "--db", journal, "--ticks", "0", *base,
                       "--expected-head", state["journal_head"])
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        self.assertEqual(json.loads(reopened.stdout), state)


if __name__ == "__main__":
    unittest.main()
