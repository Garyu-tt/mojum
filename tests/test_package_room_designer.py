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

from tools.package_room_designer import (
    HTML, JS, SCRIPT_TAG, PackagingError, standalone_html, write_standalone,
)

ROOT = Path(__file__).resolve().parents[1]


class StandaloneDesignerPackagingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mojum-singlefile-room-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.out = self.tmp / "room-designer-offline.html"

    def test_binary_build_matches_literal_source_inlining_and_preserves_privacy(self):
        html = HTML.read_text(encoding="utf-8")
        js = JS.read_text(encoding="utf-8")
        self.assertEqual(html.count(SCRIPT_TAG), 1)
        result = standalone_html()
        text = result.decode("utf-8")
        expected = html.replace(
            SCRIPT_TAG, "<script>\n" + js.rstrip() + "\n</script>", 1)
        self.assertEqual(text, expected)
        self.assertNotIn(SCRIPT_TAG, text)
        self.assertIn("MojumRoomDesignerCore", text)
        self.assertIn('id="import-file"', text)
        self.assertIn('id="download"', text)
        self.assertIn('mojum.world.point-room-draft/1', text)
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
        source.write_text(template, encoding="utf-8")
        js.write_text(good_js + "\n// </script>", encoding="utf-8")
        with self.assertRaisesRegex(PackagingError, "unsafe HTML delimiter"):
            standalone_html(html_path=source, js_path=js)
        js.write_text("console.log('not room editor')", encoding="utf-8")
        with self.assertRaisesRegex(PackagingError, "wrong room"):
            standalone_html(html_path=source, js_path=js)

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
