"""Offline proof that Cezar's public gh adapter cannot write or leave Mojum."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr

MODULE = Path(__file__).resolve().parents[1] / "tools" / "cezar_gh_public_read.py"
spec = importlib.util.spec_from_file_location("cezar_gh_public_read", MODULE)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class CezarGhPublicReadTests(unittest.TestCase):
    def test_pinned_repository_identity(self):
        self.assertEqual(
            adapter.parse(["repo", "view", "--json", "nameWithOwner"]),
            ("repo", None, False))

    def test_search_for_public_mojum_prs_is_allowed(self):
        mode, request, include = adapter.parse([
            "api", "--method", "GET", "/search/issues",
            "-f", "q=repo:Garyu-tt/mojum is:pr created:>=2026-11-20",
            "-f", "per_page=5", "-f", "sort=created", "-f", "order=asc",
        ])
        self.assertEqual(mode, "api")
        self.assertEqual(request[1], 90)
        self.assertTrue(request[0].startswith("https://api.github.com/search/issues?"))
        self.assertIn("is%3Apr", request[0])
        self.assertIn("is%3Aopen", request[0])  # no obsolete closed PR triage
        self.assertFalse(include)

    def test_issue_label_updates_and_explicit_closed_events_are_unchanged(self):
        for query in ("repo:Garyu-tt/mojum is:issue updated:>=2026-11-21",
                      "repo:Garyu-tt/mojum is:pr closed:>=2026-11-21"):
            _, request, _ = adapter.parse([
                "api", "--method", "GET", "/search/issues", "-f", "q=" + query
            ])
            self.assertNotIn("is%3Aopen", request[0])

    def test_read_only_label_timeline_with_headers(self):
        mode, request, include = adapter.parse([
            "api", "--include", "--method", "GET",
            "repos/Garyu-tt/mojum/issues/478/timeline",
            "-H", "Accept: application/vnd.github+json",
            "-f", "per_page=100", "-f", "page=2",
        ])
        self.assertEqual(mode, "api")
        self.assertTrue(request[0].startswith("https://api.github.com/repos/Garyu-tt/mojum/issues/478/timeline?"))
        self.assertEqual(request[1], 600)
        self.assertTrue(include)

    def test_writes_auth_and_cross_repo_requests_are_refused(self):
        forbidden = [
            ["pr", "create"], ["pr", "merge", "478"], ["issue", "edit", "478"],
            ["api", "--method", "POST", "/repos/Garyu-tt/mojum/issues/478/comments"],
            ["api", "--method", "DELETE", "/repos/Garyu-tt/mojum/issues/478"],
            ["api", "--method", "GET", "/repos/Garyu-tt/mojum/contents/README.md"],
            ["api", "--method", "GET", "/search/issues", "-f",
             "q=repo:another/repo is:pr"],
            ["api", "--method", "GET", "/search/issues", "-f",
             "q=repo:Garyu-tt/mojum is:pr OR repo:another/repo is:pr"],
            ["api", "--method", "GET", "/search/issues", "-f",
             "q=repo:Garyu-tt/mojum is:issue or repo:another/repo is:issue"],
            ["api", "--method", "GET", "/search/issues", "-f",
             "q=repo:Garyu-tt/mojum is:pr OR repo:another/repo"],
            ["api", "--method", "GET", "/search/issues", "-f",
             "q=repo:Garyu-tt/mojum is:pr repo:another/repo"],
            ["api", "--method", "GET", "/search/issues", "-f",
             "q=repo:Garyu-tt/mojum is:pr -org:another"],
            ["api", "--method", "GET", "/search/issues", "-f",
             "q=repo:Garyu-tt/mojum is:issue -repo:Garyu-tt/mojum"],
            ["api", "--method", "GET", "/search/issues", "-f",
             "q=repo:Garyu-tt/mojum is:issue NOT label:cezar-ready"],
            ["api", "--method", "GET", "repos/another/repo/issues/478/timeline"],
            ["repo", "view", "other/repo", "--json", "nameWithOwner"],
        ]
        for args in forbidden:
            with self.subTest(args=args), self.assertRaises(adapter.Refused):
                adapter.parse(args)

    def test_limits_and_unapproved_headers_fail_closed(self):
        invalid = [
            ["api", "--method", "GET", "/search/issues", "-f",
             "q=repo:Garyu-tt/mojum is:pr", "-f", "per_page=101"],
            ["api", "--method", "GET", "/search/issues", "-f",
             "q=repo:Garyu-tt/mojum is:issue", "-f", "page=11"],
            ["api", "--method", "GET", "/search/issues", "-f",
             "q=repo:Garyu-tt/mojum is:pr", "-f", "q=duplicated"],
            ["api", "--method", "GET", "/search/issues", "-H",
             "Authorization: token secret", "-f", "q=repo:Garyu-tt/mojum is:pr"],
            ["api", "--method", "GET", "/search/issues", "-f",
             "q=repo:Garyu-tt/mojum " + "x"*500 + " is:issue"],
            ["api", "--method", "GET", "/search/issues", "-f",
             "q=repo:Garyu-tt/mojum unrelated"],
        ]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(adapter.Refused):
                adapter.parse(args)

    def test_public_cache_never_authenticates_and_reuses_response(self):
        class FakeResponse:
            status = 200
            headers = {"Link": "<https://api.github.com/next>; rel=\"next\""}
            def __init__(self, url):
                self.url = url
            def geturl(self):
                return self.url
            def read(self, count):
                return b'{"items":[]}'
            def __enter__(self):
                return self
            def __exit__(self, *unused):
                return False

        class FakeClient:
            def __init__(self):
                self.calls = 0
            def open(self, req, timeout):
                self.calls += 1
                self_url = req.full_url
                self.assert_header = dict(req.header_items())
                return FakeResponse(self_url)

        target = "https://api.github.com/search/issues?q=repo%3AGaryu-tt%2Fmojum+is%3Apr"
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            with patch.object(adapter, "CACHE_DIR", Path(tmp)), patch.object(
                    adapter.urllib.request, "build_opener", return_value=client):
                first, link = adapter.read(target, ttl=300)
                second, _ = adapter.read(target, ttl=300)
                self.assertEqual(first, b'{"items":[]}')
                self.assertEqual(second, first)
                self.assertEqual(client.calls, 1)
                self.assertIn('rel="next"', link)
                self.assertFalse(any("Authorization" in str(a) for a in
                                     client.assert_header.items()))

    def test_refused_cli_does_not_echo_sensitive_arguments(self):
        secret = "token_please_never_log_this"
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = adapter.main(["api", "--method", "POST", "/x", "-f", secret])
        self.assertEqual(code, 2)
        self.assertNotIn(secret, errors.getvalue())
        self.assertIn("Refused", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
