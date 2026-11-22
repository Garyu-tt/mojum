import os
import sys
import subprocess
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import patch
from tools.cpu_legion import generate, local_url, parallel_limit, safe_file, task_context, verify
from tools import cpu_legion as legion

class CpuLegionTests(unittest.TestCase):
    def test_only_loopback(self):
        good = "http://127.0.0.1:8080/v1/chat/completions"
        self.assertEqual(local_url(good), good)
        for bad in ("https://api.example.org/v1/chat/completions",
                    "http://1.2.3.4:8080/v1/chat/completions",
                    "http://localhost:8080/v1/chat/completions",
                    "http://user:pass@127.0.0.1:8080/v1/chat/completions"):
            with self.assertRaises(ValueError):
                local_url(bad)

    def test_model_redirect_does_not_leave_configured_loopback_endpoint(self):
        class RedirectingModel(BaseHTTPRequestHandler):
            followed = False

            def do_POST(self):
                self.send_response(302)
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{self.server.server_port}/unexpected",
                )
                self.end_headers()

            def do_GET(self):
                type(self).followed = True
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_):
                pass

        server = HTTPServer(("127.0.0.1", 0), RedirectingModel)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = (
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
            )
            with self.assertRaises(HTTPError) as error:
                generate(endpoint, "fixture", "hello", 2)
            self.assertEqual(error.exception.code, 302)
            self.assertFalse(RedirectingModel.followed)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_model_normal_loopback_response_still_works(self):
        class Model(BaseHTTPRequestHandler):
            def do_POST(self):
                import json
                payload = json.dumps({
                    "choices": [{"message": {"content": "diff --git"}}],
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        server = HTTPServer(("127.0.0.1", 0), Model)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = (
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
            )
            self.assertEqual(generate(endpoint, "fixture", "hello", 2),
                             "diff --git")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_model_request_ignores_environment_http_proxy(self):
        class Model(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = b'{"choices":[{"message":{"content":"direct"}}]}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        class Proxy(BaseHTTPRequestHandler):
            used = False

            def do_POST(self):
                type(self).used = True
                self.send_response(502)
                self.end_headers()

            def log_message(self, *_):
                pass

        model = HTTPServer(("127.0.0.1", 0), Model)
        proxy = HTTPServer(("127.0.0.1", 0), Proxy)
        model_thread = Thread(target=model.serve_forever, daemon=True)
        proxy_thread = Thread(target=proxy.serve_forever, daemon=True)
        model_thread.start()
        proxy_thread.start()
        try:
            endpoint = f"http://127.0.0.1:{model.server_port}/v1/chat/completions"
            proxy_url = f"http://127.0.0.1:{proxy.server_port}"
            with patch.dict(os.environ, {
                "http_proxy": proxy_url,
                "HTTP_PROXY": proxy_url,
                "no_proxy": "",
                "NO_PROXY": "",
            }):
                self.assertEqual(generate(endpoint, "fixture", "hello", 2),
                                 "direct")
            self.assertFalse(Proxy.used)
        finally:
            model.shutdown()
            proxy.shutdown()
            model.server_close()
            proxy.server_close()
            model_thread.join(timeout=2)
            proxy_thread.join(timeout=2)

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


    @unittest.skipUnless(os.name == "posix" and getattr(os, "O_NOFOLLOW", 0)
                         and getattr(os, "O_DIRECTORY", 0),
                         "requires POSIX pinned descriptor reads")
    def test_context_content_is_read_from_pinned_regular_inode(self):
        self.assertEqual(legion._read_context_file(self.root, "src/test.py"), "n = 1\n")
        self.assertIn("n = 1", task_context(self.root, {
            "payload": '{"paths":["src/test.py"],"instruction":"change value"}'
        })[0])

    @unittest.skipUnless(os.name == "posix" and getattr(os, "O_NOFOLLOW", 0),
                         "requires POSIX nofollow")
    def test_context_rejects_final_symlink_swapped_after_preflight(self):
        original_safe = legion.safe_file
        secret = self.root / "secret.py"
        secret.write_text("PASSWORD=private\n")
        path = self.root / "src/test.py"

        def swap(root, name, **kwargs):
            validated = original_safe(root, name, **kwargs)
            path.unlink()
            path.symlink_to(secret)
            return validated

        with patch("tools.cpu_legion.safe_file", side_effect=swap):
            with self.assertRaises(ValueError):
                legion._read_context_file(self.root, "src/test.py")

    @unittest.skipUnless(os.name == "posix" and getattr(os, "O_NOFOLLOW", 0),
                         "requires POSIX pinned descriptor reads")
    def test_context_rejects_regular_inode_replacement_after_preflight(self):
        original_safe = legion.safe_file
        replacement = self.root / "private_replacement.py"
        replacement.write_text("PRIVATE_MARKER=leak\\n")
        target = self.root / "src/test.py"

        def swap(root, name, **kwargs):
            validated = original_safe(root, name, **kwargs)
            os.replace(replacement, target)
            return validated

        with patch("tools.cpu_legion.safe_file", side_effect=swap):
            with self.assertRaisesRegex(ValueError, "inode changed"):
                legion._read_context_file(self.root, "src/test.py")

    @unittest.skipUnless(os.name == "posix" and getattr(os, "O_NOFOLLOW", 0),
                         "requires POSIX nofollow")
    def test_context_rejects_ancestor_symlink_swapped_after_preflight(self):
        original_safe = legion.safe_file
        src = self.root / "src"
        alternate = self.root / "alternate"
        alternate.mkdir()
        (alternate / "test.py").write_text("PASSWORD=private\n")

        def swap(root, name, **kwargs):
            validated = original_safe(root, name, **kwargs)
            src.rename(self.root / "src_saved")
            src.symlink_to(alternate, target_is_directory=True)
            return validated

        with patch("tools.cpu_legion.safe_file", side_effect=swap):
            with self.assertRaises(ValueError):
                legion._read_context_file(self.root, "src/test.py")

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo"),
                         "requires POSIX FIFO")
    def test_context_swapped_fifo_cannot_hang_reader(self):
        script = """
import os
import sys
from pathlib import Path
from tools import cpu_legion as legion
root = Path(sys.argv[1])
original_safe = legion.safe_file
def swap(root, name, **kwargs):
    checked = original_safe(root, name, **kwargs)
    path = root / name
    path.unlink()
    os.mkfifo(path)
    return checked
legion.safe_file = swap
try:
    legion._read_context_file(root, "src/test.py")
except ValueError:
    sys.exit(0)
sys.exit(1)
"""
        proc = subprocess.run(
            [sys.executable, "-c", script, str(self.root)], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=False, timeout=5,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    @unittest.skipUnless(os.name == "posix", "requires POSIX descriptor reads")
    def test_context_refuses_growth_after_preflight(self):
        original_safe = legion.safe_file
        path = self.root / "src/test.py"

        def grow(root, name, **kwargs):
            validated = original_safe(root, name, **kwargs)
            path.write_bytes(b"x" * 16001)
            return validated

        with patch("tools.cpu_legion.safe_file", side_effect=grow):
            with self.assertRaises(ValueError):
                legion._read_context_file(self.root, "src/test.py")


if __name__ == "__main__":
    unittest.main()
