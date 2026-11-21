"""Hermetic GPU Legion loopback HTTP canary tests. No LLM, GPU or credentials."""
import contextlib
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest

import importlib.util

# Resource-warning audit runs from tests/ with PYTHONPATH=src, not repository root.
SOURCE = Path(__file__).resolve().parents[1] / "tools" / "gpu_legion_canary.py"
spec = importlib.util.spec_from_file_location("gpu_legion_canary", SOURCE)
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.calls += 1
        length = int(self.headers.get("Content-Length", "0"))
        self.server.request_body = self.rfile.read(min(length, 8192))
        if self.server.redirect:
            self.send_response(302)
            self.send_header("Location", "https://example.invalid/do-not-follow")
            self.end_headers()
            return
        if self.server.delay:
            time.sleep(self.server.delay)
        body = self.server.reply
        self.send_response(self.server.status_code)
        self.send_header("Content-Type", self.server.content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.get_calls += 1
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


class LoopbackFixture(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.calls = 0
        self.server.get_calls = 0
        self.server.request_body = b""
        self.server.redirect = False
        self.server.delay = 0
        self.server.status_code = 200
        self.server.content_type = "application/json"
        self.server.reply = json.dumps({
            "model": "unverified-server-claimed-model",
            "choices": [{"message": {"content": "hello world"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 2},
        }).encode()
        self.url = ("http://127.0.0.1:%d/v1/chat/completions"
                    % self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class CanaryTests(LoopbackFixture):
    def test_success_never_logs_generated_content_or_claims_model_provenance(self):
        result = canary.call_once(self.url, "gpt-oss-20b", 4)
        self.assertTrue(result["success"])
        self.assertFalse(result["content_logged"])
        self.assertEqual(result["completion_tokens_reported"], 2)
        self.assertNotIn("hello world", json.dumps(result))
        payload = json.loads(self.server.request_body)
        self.assertEqual(payload["messages"][0]["content"], canary.CANARY_PROMPT)
        self.assertFalse(payload["stream"])
        report = canary.run_canary([self.url], timeout=4)
        self.assertEqual(report["attempts"], 1)
        self.assertEqual(report["successful_responses"], 1)
        self.assertFalse(report["model_identity_attested"])
        self.assertFalse(report["model_artifact_verified"])
        self.assertFalse(report["deployment_performed"])
        self.assertFalse(report["gpu_compute_concurrency_verified"])

    def test_reject_untrusted_urls(self):
        forbidden = [
            "https://127.0.0.1:18880/v1/chat/completions",
            "http://localhost:18880/v1/chat/completions",
            "http://127.0.0.2:18880/v1/chat/completions",
            "http://10.0.0.2:18880/v1/chat/completions",
            "http://user:pass@127.0.0.1:18880/v1/chat/completions",
            "http://127.0.0.1:18880/v1/models",
            "http://127.0.0.1:18880/v1/chat/completions?a=1",
            "http://127.0.0.1:18880/v1/chat/completions#fragment",
            "http://127.0.0.1:1/v1/chat/completions",
            "http://127.0.0.1:99999/v1/chat/completions",
        ]
        for bad in forbidden:
            with self.subTest(bad=bad), self.assertRaises(canary.CanaryError):
                canary.local_endpoint(bad)
        self.assertEqual(canary.local_endpoint(self.url), self.url)

    def test_redirect_never_follows(self):
        self.server.redirect = True
        with self.assertRaises(canary.CanaryError):
            canary.call_once(self.url, "gpt-oss-20b", 4)
        self.assertEqual(self.server.calls, 1)
        self.assertEqual(self.server.get_calls, 0)

    def test_refuse_non_json_content_type(self):
        self.server.content_type = "text/html"
        with self.assertRaisesRegex(canary.CanaryError, "not JSON"):
            canary.call_once(self.url, "gpt-oss-20b", 4)

    def test_refuse_oversize_response(self):
        self.server.reply = b"x" * (canary.MAX_REPLY_BYTES + 1)
        with self.assertRaisesRegex(canary.CanaryError, "too large"):
            canary.call_once(self.url, "gpt-oss-20b", 4)

    def test_refuse_empty_response(self):
        self.server.reply = b"{}"
        with self.assertRaisesRegex(canary.CanaryError, "choices"):
            canary.call_once(self.url, "gpt-oss-20b", 4)

    def test_refuse_forged_token_numbers(self):
        self.server.reply = json.dumps({
            "choices": [{"message": {"content": "hello world"}}],
            "usage": {"completion_tokens": True}
        }).encode()
        with self.assertRaisesRegex(canary.CanaryError, "invalid completion"):
            canary.call_once(self.url, "gpt-oss-20b", 4)

    def test_successful_parallel_http_overlap_not_gpu_proof(self):
        self.server.delay = 0.08
        report = canary.run_canary([self.url], samples=2, parallel=2, timeout=5)
        self.assertEqual(report["attempts"], 2)
        self.assertEqual(report["successful_responses"], 2)
        self.assertGreaterEqual(report["peak_overlapping_http_calls"], 2)
        self.assertFalse(report["gpu_compute_concurrency_verified"])

    def test_wrong_status_is_reported_as_failed_without_leaking_text(self):
        self.server.status_code = 503
        report = canary.run_canary([self.url], timeout=4)
        self.assertEqual(report["failed_responses"], 1)
        self.assertEqual(report["peak_overlapping_http_calls"], 1)
        self.assertEqual(report["peak_overlapping_successful_http_calls"], 0)
        self.assertFalse(report["results"][0]["content_logged"])

    def test_overlapping_failed_http_calls_are_not_invisible(self):
        self.server.status_code = 503
        self.server.delay = 0.2
        report = canary.run_canary([self.url], samples=2, parallel=2, timeout=5)
        self.assertEqual(report["successful_responses"], 0)
        self.assertEqual(report["failed_responses"], 2)
        self.assertEqual(report["peak_overlapping_http_calls"], 2)
        self.assertEqual(report["peak_overlapping_successful_http_calls"], 0)
        for item in report["results"]:
            self.assertLess(item["start_ns"], item["end_ns"])
            self.assertFalse(item["content_logged"])

    def test_bad_parameters_fail_before_http_calls(self):
        invalid = [
            {"samples": 65}, {"samples": True}, {"parallel": 17},
            {"parallel": 0}, {"timeout": 0}, {"timeout": 61},
            {"model": ""}, {"model": "m" * 129},
        ]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(canary.CanaryError):
                canary.run_canary([self.url], **kwargs)
        with self.assertRaises(canary.CanaryError):
            canary.run_canary([self.url, self.url])
        self.assertEqual(self.server.calls, 0)

    def test_mixed_success_failure_overlap_is_two_attempts(self):
        events = [
            {"success": False, "start_ns": 100, "end_ns": 300},
            {"success": True, "start_ns": 150, "end_ns": 250},
        ]
        self.assertEqual(canary.peak_overlap(events), 2)
        self.assertEqual(canary.peak_overlap(events, successful_only=True), 1)

    def test_interval_validation(self):
        with self.assertRaisesRegex(canary.CanaryError, "interval"):
            canary.peak_overlap([{"success": True, "start_ns": 8, "end_ns": 2}])
        self.assertEqual(canary.peak_overlap([
            {"success": True, "start_ns": 1, "end_ns": 4},
            {"success": True, "start_ns": 2, "end_ns": 5},
            {"success": False, "start_ns": 2, "end_ns": 7}]), 2)

    def test_cli_handles_success_and_invalid_endpoint(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = canary.main(["--endpoint", self.url, "--timeout", "4"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(out.getvalue())["successful_responses"], 1)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status = canary.main(["--endpoint", "http://example.invalid:8080/v1/chat/completions"])
        self.assertEqual(status, 2)
        self.assertFalse(json.loads(err.getvalue())["deployment_performed"])


if __name__ == "__main__":
    unittest.main()
