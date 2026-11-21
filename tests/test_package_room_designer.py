"""Portable one-file SVG editor: exact build, integrity, no-clobber, no DB.

All files are freshly generated under isolated local temp directories; only
the checked-in synthetic UI sources are read.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import importlib.util

ROOT = Path(__file__).resolve().parents[1]
# The repository's SQLite leak auditor loads test modules directly from
# tests/ with PYTHONPATH=src; repo-root 'tools' is not on sys.path there.
# Load the pinned local standalone packager by absolute source path without
# mutating sys.path or weakening the independent audit.
_spec = importlib.util.spec_from_file_location(
    "_mojum_room_packager", ROOT / "tools" / "package_room_designer.py"
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load local room packager")
_packager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_packager)
HTML, JS, SCRIPT_TAG, HISTORY_JS, HISTORY_TAG, WALK_JS, WALK_TAG = (
    _packager.HTML, _packager.JS, _packager.SCRIPT_TAG,
    _packager.HISTORY_JS, _packager.HISTORY_TAG,
    _packager.WALK_JS, _packager.WALK_TAG,
)
PackagingError = _packager.PackagingError
standalone_html = _packager.standalone_html
write_standalone = _packager.write_standalone


class StandaloneDesignerPackagingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mojum-singlefile-room-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.out = self.tmp / "room-designer-offline.html"

    def test_binary_build_matches_literal_source_inlining_and_preserves_privacy(self):
        html = HTML.read_text(encoding="utf-8")
        scripts = (
            (SCRIPT_TAG, JS, "MojumRoomDesignerCore"),
            (HISTORY_TAG, HISTORY_JS, "MojumRoomHistory"),
            (WALK_TAG, WALK_JS, "MojumWalkPreview"),
        )
        result = standalone_html()
        text = result.decode("utf-8")
        expected = html
        for tag, source, symbol in scripts:
            self.assertEqual(html.count(tag), 1)
            expected = expected.replace(
                tag, "<script>\n" + source.read_text(encoding="utf-8").rstrip()
                + "\n</script>", 1)
            self.assertNotIn(tag, text)
            self.assertIn(symbol, text)
        self.assertEqual(text, expected)
        self.assertNotRegex(text, r"<script\b[^>]*\bsrc\b")
        self.assertIn('id="import-file"', text)
        self.assertIn('id="download"', text)
        self.assertIn('mojum.world.point-room-draft/1', text)
        # Current main has no typed-room journal CLI. Never bundle a stale
        # command suggesting the authoring draft can be persisted as SQLite.
        self.assertNotIn("python -m mojum.room_input", text)
        self.assertNotIn("--new-room-contract", text)
        self.assertNotIn('src="http', text)
        self.assertNotIn('href="http', text)
        self.assertNotIn("localStorage.", text)
        self.assertNotIn("indexedDB.", text)
        self.assertEqual(standalone_html(), result)

    def test_can_write_exactly_once_with_sha_and_owner_only_permissions(self):
        expected = standalone_html()
        response = write_standalone(self.out)
        self.assertEqual(response["bytes"], len(expected))
        self.assertEqual(response["sha256"], hashlib.sha256(expected).hexdigest())
        self.assertEqual(self.out.read_bytes(), expected)
        self.assertEqual(response["file"], str(self.out))
        self.assertEqual(
            response["status"], "NEW_STANDALONE_OFFLINE_ROOM_DESIGNER_WRITTEN")
        if os.name == "posix":
            self.assertEqual(self.out.stat().st_mode & 0o777, 0o600)
        before = self.out.read_bytes()
        with self.assertRaisesRegex(PackagingError, "refusing to overwrite"):
            write_standalone(self.out)
        self.assertEqual(self.out.read_bytes(), before)
        self.assertFalse(list(self.tmp.glob("*.part")))
        self.assertFalse(list(self.tmp.glob(".mojum-room-*.part")))

    def test_missing_parent_or_non_html_does_not_create_any_output(self):
        with self.assertRaisesRegex(PackagingError, "new .html"):
            write_standalone(self.tmp / "not-html.txt")
        missing = self.tmp / "nonexistent" / "room.html"
        with self.assertRaisesRegex(PackagingError, "existing nonsymlink"):
            write_standalone(missing)
        self.assertFalse(missing.exists())

    def test_refuses_foreign_symlink_destination_without_modifying_target(self):
        foreign = self.tmp / "unrelated-private.txt"
        foreign.write_bytes(b"unrelated private history must survive")
        try:
            self.out.symlink_to(foreign)
        except OSError:
            self.skipTest("host does not permit symlinks")
        with self.assertRaisesRegex(PackagingError, "refusing to overwrite"):
            write_standalone(self.out)
        self.assertEqual(foreign.read_bytes(), b"unrelated private history must survive")

    def test_source_changes_are_checked_fail_closed(self):
        source = self.tmp / "template.html"
        js = self.tmp / "script.js"
        template = HTML.read_text(encoding="utf-8")
        good_js = JS.read_text(encoding="utf-8")
        source.write_text(template.replace(SCRIPT_TAG, ""), encoding="utf-8")
        js.write_text(good_js, encoding="utf-8")
        with self.assertRaisesRegex(PackagingError, "exactly one"):
            standalone_html(html_path=source, js_path=js)
        source.write_text(template.replace(HISTORY_TAG, ""), encoding="utf-8")
        with self.assertRaisesRegex(PackagingError, "exactly one"):
            standalone_html(html_path=source, js_path=js)
        source.write_text(template.replace(WALK_TAG, ""), encoding="utf-8")
        with self.assertRaisesRegex(PackagingError, "exactly one"):
            standalone_html(html_path=source, js_path=js)
        source.write_text(template, encoding="utf-8")
        js.write_text(good_js + "\n// </script>", encoding="utf-8")
        with self.assertRaisesRegex(PackagingError, "unsafe HTML delimiter"):
            standalone_html(html_path=source, js_path=js)
        js.write_text("console.log('not room editor')", encoding="utf-8")
        with self.assertRaisesRegex(PackagingError, "wrong room"):
            standalone_html(html_path=source, js_path=js)

    def test_unknown_script_tag_quoting_and_attributes_fail_closed(self):
        template = HTML.read_text(encoding="utf-8")
        html = self.tmp / "malformed-template.html"
        for rogue in (
            "<script src='./unknown.js'></script>",
            '<script defer src="./unknown.js"></script>',
            '<SCRIPT SRC="./unknown.js"></SCRIPT>',
            '<script type="module" src="./unknown.js"></script>',
            '<script\nsrc="./unknown.js"></script>',
            '<script title=">" src="./unknown.js"></script>',
            '<script data-note="> trusted" SRC=./unknown.js></script>',
        ):
            with self.subTest(rogue=rogue):
                html.write_text(
                    template.replace(SCRIPT_TAG, SCRIPT_TAG + "\n" + rogue, 1),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(PackagingError, "unexpected"):
                    standalone_html(html_path=html)
        self.assertFalse(self.out.exists())

    def test_two_subprocesses_have_exactly_one_successful_exclusive_publish(self):
        cmd = [
            sys.executable, str(ROOT / "tools/package_room_designer.py"),
            "--out", str(self.out),
        ]
        children = [
            subprocess.Popen(
                cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            ) for _ in range(2)
        ]
        output = [c.communicate(timeout=20) for c in children]
        codes = [c.returncode for c in children]
        self.assertEqual(sorted(codes), [0, 2], (codes, output))
        good = json.loads(output[codes.index(0)][0])
        self.assertEqual(self.out.stat().st_size, good["bytes"])
        self.assertEqual(hashlib.sha256(self.out.read_bytes()).hexdigest(),
                         good["sha256"])
        self.assertFalse(list(self.tmp.glob(".mojum-room-*.part")))


if __name__ == "__main__":
    unittest.main()
