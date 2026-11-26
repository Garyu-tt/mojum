"""Regression tests for deterministic rejected HTTP response cleanup."""

import io
import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

from tools import cpu_legion
from tools import gpu_legion_canary


ENDPOINT = "http://127.0.0.1:18080/v1/chat/completions"


class _RaisingOpener:
    def __init__(self, error):
        self.error = error

    def open(self, request, timeout):
        raise self.error


def _http_error(code, message):
    body = io.BytesIO(b"synthetic response body must not remain open")
    headers = Message()
    headers["Content-Type"] = "text/plain"
    return HTTPError(ENDPOINT, code, message, headers, body), body


class LegionHttpResponseCleanupTests(unittest.TestCase):
    def test_cpu_generate_closes_body_and_reraises_same_http_error(self):
        error, body = _http_error(302, "Found")
        opener = _RaisingOpener(error)

        with patch.object(cpu_legion, "build_opener", return_value=opener):
            with self.assertRaises(HTTPError) as raised:
                cpu_legion.generate(ENDPOINT, "fixture", "hello", 2)

        self.assertIs(raised.exception, error)
        self.assertEqual(raised.exception.code, 302)
        self.assertTrue(body.closed)

    def test_gpu_call_closes_body_and_preserves_redacted_error_contract(self):
        error, body = _http_error(503, "Service Unavailable")

        with self.assertRaisesRegex(
            gpu_legion_canary.CanaryError,
            r"^model endpoint returned HTTP 503$",
        ) as raised:
            gpu_legion_canary.call_once(
                ENDPOINT,
                "gpt-oss-20b",
                2,
                opener=_RaisingOpener(error),
            )

        self.assertIs(raised.exception.__cause__, error)
        self.assertEqual(error.code, 503)
        self.assertTrue(body.closed)


if __name__ == "__main__":
    unittest.main()
