"""Offline proof that Cezar's public gh adapter cannot write or leave Mojum."""
import importlib.util
import hashlib
import io
import json
import os
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

    def test_explicit_closed_pr_created_query_remains_closed(self):
        # PRs created in the time window may have closed since; these are
        # historical queries, not new-open-PR intake.
        from urllib.parse import parse_qs, urlsplit
        for state in ("is:closed", "state:closed"):
            query = ("repo:Garyu-tt/mojum is:pr " + state
                     + " created:>=2026-11-20")
            with self.subTest(state=state):
                _, request, _ = adapter.parse([
                    "api", "--method", "GET", "/search/issues",
                    "-f", "q=" + query])
                result_q = parse_qs(urlsplit(request[0]).query)["q"][0]
                self.assertEqual(result_q, query)
                self.assertNotIn("is:open", result_q)

    def test_created_pr_with_explicit_closed_date_stays_historical(self):
        from urllib.parse import parse_qs, urlsplit
        for suffix in ("closed:>=2026-11-21", "is:open", "state:open"):
            with self.subTest(suffix=suffix):
                query = ("repo:Garyu-tt/mojum is:pr created:>=2026-11-20 "
                         + suffix)
                _, request, _ = adapter.parse([
                    "api", "--method", "GET", "/search/issues", "-f", "q=" + query])
                self.assertEqual(
                    parse_qs(urlsplit(request[0]).query)["q"], [query])

    def test_non_intake_created_window_does_not_gain_open_filter(self):
        from urllib.parse import parse_qs, urlsplit
        query = "repo:Garyu-tt/mojum is:pr created:<=2026-11-20"
        _, request, _ = adapter.parse([
            "api", "--method", "GET", "/search/issues", "-f", "q=" + query])
        self.assertEqual(parse_qs(urlsplit(request[0]).query)["q"], [query])

    def test_conflicting_pr_states_fail_closed(self):
        query = ("repo:Garyu-tt/mojum is:pr is:closed "
                 "state:open created:>=2026-11-20")
        with self.assertRaisesRegex(adapter.Refused, "conflicting"):
            adapter.parse(["api", "--method", "GET", "/search/issues",
                           "-f", "q=" + query])

    def test_rejects_search_boolean_scope_escape(self):
        forbidden = [
            "repo:Garyu-tt/mojum is:pr OR repo:another/repo is:pr",
            "repo:Garyu-tt/mojum is:pr -repo:Garyu-tt/mojum",
            "repo:Garyu-tt/mojum is:pr OR (is:issue)",
            "repo:Garyu-tt/mojum is:pr user:another",
            "repo:Garyu-tt/mojum is:pr org:another",
            "repo:Garyu-tt/mojum is:pr is:issue",
        ]
        for query in forbidden:
            with self.subTest(query=query), self.assertRaises(adapter.Refused):
                adapter.parse(["api", "--method", "GET", "/search/issues",
                               "-f", "q=" + query])

    def test_allows_bounded_single_repo_filters(self):
        query = ("repo:Garyu-tt/mojum is:issue state:open label:cezar-ready "
                 "-label:cezar-hold updated:>=2026-11-20")
        mode, request, include = adapter.parse(
            ["api", "--method", "GET", "/search/issues", "-f", "q=" + query])
        self.assertEqual(mode, "api")
        self.assertFalse(include)
        from urllib.parse import parse_qs, urlsplit
        self.assertEqual(parse_qs(urlsplit(request[0]).query)["q"], [query])

    def test_created_pr_query_preserves_explicit_closed_and_open_filters(self):
        from urllib.parse import parse_qs, urlsplit
        # Created-date triage may default to open PRs only when the caller
        # did not explicitly ask for a historical closed subset.
        for suffix in ("is:closed", "state:closed", "is:open", "state:open",
                       "closed:>=2026-11-21"):
            query = ("repo:Garyu-tt/mojum is:pr created:>=2026-11-20 "
                     + suffix)
            with self.subTest(suffix=suffix):
                mode, request, include = adapter.parse([
                    "api", "--method", "GET", "/search/issues",
                    "-f", "q=" + query,
                ])
                self.assertEqual(mode, "api")
                self.assertFalse(include)
                self.assertEqual(parse_qs(urlsplit(request[0]).query)["q"],
                                 [query])

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

    def test_cached_response_cannot_be_swapped_to_unrelated_file(self):
        # Inject the swap at the real descriptor open, not at the obsolete
        # Path.is_symlink preflight: the old mock never ran on the fixed code.
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir(mode=0o700)
            url = adapter.BASE + "/search/issues?q=repo%3AGaryu-tt%2Fmojum"
            filename = hashlib.sha256(url.encode()).hexdigest() + ".json"
            cached = cache_dir / filename
            cached.write_text(json.dumps({
                "body": '{"kind":"trusted"}', "link": "",
            }), encoding="utf-8")
            cached.chmod(0o600)
            foreign = Path(tmp) / "foreign.json"
            foreign.write_text(json.dumps({
                "body": '{"kind":"swapped"}', "link": "",
            }), encoding="utf-8")
            foreign.chmod(0o600)
            original_open = os.open
            swapped = False

            def swap_at_actual_open(path, flags, *args, **kwargs):
                nonlocal swapped
                if path == filename and kwargs.get("dir_fd") is not None:
                    # Simulate same-UID attacker replacing the directory
                    # entry after the cache directory was pinned.
                    if not swapped:
                        swapped = True
                        cached.unlink()
                        cached.symlink_to(foreign)
                return original_open(path, flags, *args, **kwargs)

            with patch.object(adapter, "CACHE_DIR", cache_dir), patch.object(
                    adapter.os, "open", side_effect=swap_at_actual_open):
                with self.assertRaisesRegex(adapter.Refused,
                                            "untrusted cache entry"):
                    adapter.read(url, ttl=300)
            self.assertTrue(swapped, "race injection never executed")
            self.assertTrue(cached.is_symlink())

    def test_cache_rejects_symlink_in_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / "private"
            directory = private / "cache"
            directory.mkdir(parents=True, mode=0o700)
            alias = root / "alias"
            alias.symlink_to(private, target_is_directory=True)
            url = adapter.BASE + "/search/issues?q=repo%3AGaryu-tt%2Fmojum"
            filename = hashlib.sha256(url.encode()).hexdigest() + ".json"
            cached = directory / filename
            cached.write_text(json.dumps({
                "body": '{"kind":"outside"}', "link": "",
            }), encoding="utf-8")
            cached.chmod(0o600)
            with patch.object(adapter, "CACHE_DIR", alias / "cache"):
                with self.assertRaises((adapter.Refused, OSError)):
                    adapter.read(url, ttl=300)

    def test_fifo_cache_entry_is_rejected_without_blocking_open(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("POSIX FIFO required")
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir(mode=0o700)
            url = adapter.BASE + "/search/issues?q=repo%3AGaryu-tt%2Fmojum"
            filename = hashlib.sha256(url.encode()).hexdigest() + ".json"
            os.mkfifo(cache_dir / filename, mode=0o600)
            actual_open = os.open

            def checked_open(path, flags, *args, **kwargs):
                if path == filename and not (flags & os.O_NONBLOCK):
                    self.fail("cached entry must be opened O_NONBLOCK before fstat")
                return actual_open(path, flags, *args, **kwargs)

            with patch.object(adapter, "CACHE_DIR", cache_dir), patch.object(
                    adapter.os, "open", side_effect=checked_open):
                with self.assertRaisesRegex(adapter.Refused, "regular"):
                    adapter.read(url, ttl=300)

    def test_symlink_ancestor_cannot_create_missing_cache_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "private"
            target.mkdir(mode=0o700)
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            child = target / "must-not-be-created"
            url = adapter.BASE + "/search/issues?q=repo%3AGaryu-tt%2Fmojum"
            with patch.object(adapter, "CACHE_DIR", alias / child.name):
                with self.assertRaises((adapter.Refused, OSError)):
                    adapter.read(url, ttl=300)
            self.assertFalse(child.exists(),
                             "unsafe mkdir followed a symlinked ancestor")

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
