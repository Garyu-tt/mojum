"""Credential-free regression checks for narrow Spartan patch preflight."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "tools" / "spartan_patch_preflight.py"
spec = importlib.util.spec_from_file_location("spartan_patch_preflight", MODULE)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def patch(path="src/mojum/hello.py", body="@@ -1 +1 @@\n-old\n+new\n", metadata=""):
    return (f"diff --git a/{path} b/{path}\n"
            f"index 1111111..2222222 100644\n{metadata}"
            f"--- a/{path}\n+++ b/{path}\n{body}").encode()


class PatchPreflightTests(unittest.TestCase):
    def check(self, data, *, allow=("src/mojum/",), checkout=None):
        return mod.inspect_patch(data, allow=allow, checkout=checkout)

    def rejects(self, data, **kwargs):
        with self.assertRaises(mod.PatchRejected):
            self.check(data, **kwargs)

    def test_valid_change(self):
        report = self.check(patch())
        self.assertEqual(report["status"], "candidate")
        self.assertEqual(report["changed_lines"], 2)
        self.assertEqual(report["files"], ["src/mojum/hello.py"])

    def test_new_regular_file(self):
        data = ("diff --git a/src/mojum/add.py b/src/mojum/add.py\n"
                "new file mode 100644\nindex 0000000..1111111\n"
                "--- /dev/null\n+++ b/src/mojum/add.py\n@@ -0,0 +1 @@\n+hello\n").encode()
        self.assertEqual(self.check(data)["changed_lines"], 1)

    def test_scope_required(self):
        self.rejects(patch(), allow=())
        self.rejects(patch("docs/hello.py"))
        self.rejects(patch(), allow=("src/mojum",))

    def test_traversal_and_dot(self):
        for name in ("src/mojum/../../.github/x", "src/mojum/.env",
                     "src/mojum//x", "src/mojum/a\\b", ".github/workflows/ci.yml"):
            with self.subTest(name=name):
                self.rejects(patch(name), allow=("src/mojum/", ".github/"))

    def test_renames_and_deletes(self):
        self.rejects(patch().replace(b"b/src/mojum/hello.py\n", b"b/src/mojum/moved.py\n", 1))
        self.rejects(patch().replace(b"+++ b/src/mojum/hello.py", b"+++ /dev/null"))

    def test_mode_and_binary(self):
        self.rejects(patch(metadata="old mode 100644\nnew mode 100755\n"))
        self.rejects(patch().replace(b"--- a/", b"GIT binary patch\n--- a/"))
        self.rejects(patch(metadata="new file mode 120000\n"))

    def test_truncated_and_overfull_hunk(self):
        self.rejects(patch(body="@@ -2,2 +2,2 @@\n-old\n+new\n"))
        self.rejects(patch(body="@@ -1 +1 @@\n-old\n+new\n+extra\n"))

    def test_reversed_hunks(self):
        self.rejects(patch(body="@@ -10 +10 @@\n-a\n+b\n@@ -1 +1 @@\n-c\n+d\n"))

    def test_empty_hunk_and_no_hunks(self):
        self.rejects(patch(body="@@ -0,0 +0,0 @@\n"))
        self.rejects(patch(body=""))

    def test_binary_encoding_and_cr(self):
        self.rejects(patch() + b"\x00")
        self.rejects(patch().replace(b"\n", b"\r\n"))
        self.rejects(patch() + b"\xff")
        self.rejects(patch()[:-1])

    def test_duplicates_and_limits(self):
        self.rejects(patch() + patch())
        self.rejects(b"a" * (mod.MAX_BYTES + 1))
        self.rejects(patch(body="@@ -0,0 +1,401 @@\n" + "+x\n" * 401))

    def test_symlink_checkout(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "src").mkdir()
            (root / "src/mojum").symlink_to(root, target_is_directory=True)
            self.rejects(patch(), checkout=root)

    def test_real_checkout_modify_create(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "src/mojum").mkdir(parents=True)
            self.rejects(patch(), checkout=root)
            (root / "src/mojum/hello.py").write_text("old\n")
            self.check(patch(), checkout=root)
            new = ("diff --git a/src/mojum/add.py b/src/mojum/add.py\n"
                   "new file mode 100644\nindex 0000000..1111111\n"
                   "--- /dev/null\n+++ b/src/mojum/add.py\n@@ -0,0 +1 @@\n+x\n").encode()
            self.check(new, checkout=root)
            (root / "src/mojum/add.py").write_text("x")
            self.rejects(new, checkout=root)

    def test_cli_exit_status(self):
        cmd = [sys.executable, str(MODULE), "--allow", "src/mojum/"]
        result = subprocess.run(cmd, input=patch(), capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b'"candidate"', result.stdout)
        bad = subprocess.run(cmd, input=patch("docs/no.py"), capture_output=True, check=False)
        self.assertEqual(bad.returncode, 2)
        self.assertIn(b'"reject"', bad.stdout)


if __name__ == "__main__":
    unittest.main()
