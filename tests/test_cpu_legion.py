import subprocess
import tempfile
import unittest
from pathlib import Path
from tools.cpu_legion import local_url, parallel_limit, safe_file, task_context, verify

class CpuLegionTests(unittest.TestCase):
    def test_only_loopback(self):
        good = "http://127.0.0.1:8080/v1/chat/completions"
        self.assertEqual(local_url(good), good)
        for bad in ("https://api.example.org/v1/chat/completions",
                    "http://1.2.3.4:8080/v1/chat/completions",
                    "http://user:pass@127.0.0.1:8080/v1/chat/completions"):
            with self.assertRaises(ValueError):
                local_url(bad)

    def test_real_capacity_not_300_fake_models(self):
        self.assertEqual(parallel_limit(300, 16, 32, 8, 10000, 1024), 4)
        self.assertEqual(parallel_limit(300, 16, 32, 8, 1000, 1024), 0)
        with self.assertRaises(ValueError):
            parallel_limit(300, 0, 32, 8, 10000, 1024)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "src").mkdir()
        (self.root / "src/test.py").write_text("n = 1\n")
        subprocess.run(["git", "-C", str(self.root), "add", "src/test.py"], check=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_disallow_escape_and_symlink(self):
        (self.root / "src/secret.py").symlink_to("/etc/passwd")
        for item in ["../../etc/passwd", ".github/workflows/publish.yml",
                     "src/secret.py", "src/missing.py"]:
            with self.assertRaises(ValueError):
                safe_file(self.root, item)

    def test_valid_patch(self):
        patch = ("diff --git a/src/test.py b/src/test.py\n"
                 "--- a/src/test.py\n+++ b/src/test.py\n"
                 "@@ -1 +1 @@\n-n = 1\n+n = 2\n")
        self.assertEqual(verify(self.root, patch, ["src/test.py"]), patch)

    def test_reject_out_of_scope(self):
        patch = ("diff --git a/src/test.py b/src/test.py\n"
                 "--- a/src/test.py\n+++ b/src/test.py\n"
                 "@@ -1 +1 @@\n-n = 1\n+n = 2\n")
        with self.assertRaises(ValueError):
            verify(self.root, patch, ["tests/another.py"])

    def test_reject_unsafe_header(self):
        patch = ("diff --git a/src/test.py b/src/test.py\n"
                 "--- a/src/test.py\n+++ b/../../etc/passwd\n"
                 "@@ -1 +1 @@\n-n = 1\n+n = 2\n")
        with self.assertRaises(ValueError):
            verify(self.root, patch, ["src/test.py"])

if __name__ == "__main__":
    unittest.main()
