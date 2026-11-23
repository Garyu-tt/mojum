"""Hermetic issue-ref reservation tests: no GitHub access required."""
from __future__ import annotations

import concurrent.futures
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
from threading import Barrier, Lock
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import github_ref_claim as c

SHA = "a" * 40
REPO = "Garyu-tt/mojum"


class FakeAPI:
    def __init__(self, *, issue_open=True, barrier=None, comment_failure=False):
        self.issue_open = issue_open
        self.barrier = barrier
        self.gated_reads = 0
        self.comment_failure = comment_failure
        self.refs = {"refs/heads/main": SHA}
        self.lock = Lock()
        self.create_calls = 0
        self.announcements = []

    def issue_is_open(self, repo, issue):
        return self.issue_open

    def ref_sha(self, repo, ref):
        gate = False
        with self.lock:
            if ref.startswith("refs/heads/claims/") and self.barrier and self.gated_reads < 2:
                self.gated_reads += 1
                gate = True
            value = self.refs.get(ref)
        if gate:
            self.barrier.wait(timeout=5)
            return None  # force both preflight reads to see an absent ref
        return value

    def create_ref(self, repo, ref, sha):
        with self.lock:
            self.create_calls += 1
            if ref in self.refs:
                raise c.ApiError(422)
            self.refs[ref] = sha

    def announce(self, repo, issue, scope, ref):
        if self.comment_failure:
            raise c.ApiError(403)
        self.announcements.append((issue, scope, ref))


class ClaimTests(unittest.TestCase):
    def test_exact_name_and_dry_run_no_credentials(self):
        ref = "refs/heads/claims/issue-478/atomic-ref"
        self.assertEqual(c.ref_name(478, "atomic-ref"), ref)
        out = StringIO()
        with patch.dict("os.environ", {"GITHUB_TOKEN": ""}), redirect_stdout(out):
            self.assertEqual(c.main(["--repo", REPO, "--issue", "478", "--scope", "atomic-ref"]), 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "dry_run")
        self.assertEqual(json.loads(out.getvalue())["ref"], ref)

    def test_invalid_issue_scope_repo_are_rejected(self):
        for issue in (0, -1, 1_000_000_000, True, "42", 2.5):
            with self.subTest(issue=issue), self.assertRaises(c.ClaimError):
                c.ref_name(issue, "safe")
        for scope in ("", "A", "has/slash", "has space", "a_", "-a", "a-", "a--b",
                      "x" * 65, "alpha\nxxx", "é", None, True):
            with self.subTest(scope=scope), self.assertRaises(c.ClaimError):
                c.ref_name(2, scope)
        for repo in ("", "a", "/b", "a/../b", "a/..", "../a", "a/b/c", "a/b?x", "a/b%2f", "a/.hidden/.."):
            with self.subTest(repo=repo), self.assertRaises(c.ClaimError):
                c.repo_name(repo)

    def test_single_claim_and_no_duplicate(self):
        api = FakeAPI()
        result = c.reserve(api, REPO, 478, "atomic-ref")
        self.assertEqual(result["status"], "reserved")
        self.assertEqual(len(api.announcements), 1)
        with self.assertRaises(c.ClaimTaken):
            c.reserve(api, REPO, 478, "atomic-ref")
        self.assertEqual(api.create_calls, 1)

    def test_distinct_scopes_are_independent(self):
        api = FakeAPI()
        self.assertEqual(c.reserve(api, REPO, 478, "alpha")["status"], "reserved")
        self.assertEqual(c.reserve(api, REPO, 478, "beta")["status"], "reserved")
        self.assertEqual(api.create_calls, 2)

    def test_concurrent_compare_race_only_one_wins(self):
        # Both workers see absent ref before either enters create_ref.
        api = FakeAPI(barrier=Barrier(2))
        def worker(_):
            try:
                return c.reserve(api, REPO, 478, "race")["status"]
            except c.ClaimTaken:
                return "taken"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, range(2)))
        self.assertCountEqual(results, ["reserved", "taken"])
        self.assertEqual(len(api.announcements), 1)

    def test_422_without_existing_ref_is_not_mislabeled_taken(self):
        api = FakeAPI()
        api.create_ref = lambda *args: (_ for _ in ()).throw(c.ApiError(422))
        with self.assertRaisesRegex(c.ApiError, "422"):
            c.reserve(api, REPO, 478, "x")

    def test_closed_or_pr_issue_never_creates(self):
        api = FakeAPI(issue_open=False)
        with self.assertRaisesRegex(c.ClaimError, "open GitHub issue"):
            c.reserve(api, REPO, 478, "x")
        self.assertEqual(api.create_calls, 0)

    def test_comment_failure_preserves_claim_no_false_success(self):
        api = FakeAPI(comment_failure=True)
        result = c.reserve(api, REPO, 478, "comment-error")
        self.assertEqual(result["status"], "reserved_comment_failed")
        self.assertIn(result["ref"], api.refs)

    def test_post_timeout_has_ambiguous_outcome_no_retry(self):
        api = FakeAPI()
        def post(repo, ref, sha):
            api.create_calls += 1
            api.refs[ref] = sha
            raise c.ClaimError("GitHub connection failure; outcome may be unknown")
        api.create_ref = post
        with self.assertRaisesRegex(c.ClaimError, "unknown"):
            c.reserve(api, REPO, 478, "timeout")
        self.assertEqual(api.create_calls, 1)
        self.assertFalse(api.announcements)

    def test_ref_sha_rejects_malformed_server_answer(self):
        api = c.GitHubAPI("mock-token")
        for answer in ({"ref": "refs/heads/main", "object": {"sha": "bad"}},
                       {"ref": "refs/heads/other", "object": {"sha": SHA}},
                       {"ref": "refs/heads/main", "object": None}):
            with self.subTest(answer=answer), patch.object(api, "_request", return_value=answer):
                with self.assertRaises(c.ClaimError):
                    api.ref_sha(REPO, "refs/heads/main")

    def test_api_error_does_not_echo_secret(self):
        api = c.GitHubAPI("do-not-print-my-token")
        class BadOpener:
            def open(self, *args, **kwargs):
                raise HTTPError("https://api.github.com/", 403, "secret=do-not-print-my-token", None, None)
        api._opener = BadOpener()
        with self.assertRaises(c.ApiError) as caught:
            api.issue_is_open(REPO, 478)
        self.assertEqual(str(caught.exception), "GitHub request refused (HTTP 403)")

    def test_network_failure_no_secret_or_remote_calls_during_dry_run(self):
        api = c.GitHubAPI("sensitive-token")
        class BadOpener:
            def open(self, *args, **kwargs):
                raise URLError("credentials: sensitive-token")
        api._opener = BadOpener()
        with self.assertRaises(c.ClaimError) as caught:
            api.issue_is_open(REPO, 478)
        self.assertNotIn("sensitive-token", str(caught.exception))

    def test_request_json_schema_and_size_bounds(self):
        api = c.GitHubAPI("safe")
        class Response:
            status = 200
            def __init__(self, data):
                self.data = data
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _): return self.data
        class Opener:
            def __init__(self, data): self.data = data
            def open(self, *_, **__): return Response(self.data)
        for raw in (b"[]", b"not JSON", b"{" + b" " * (128 * 1024) + b"}"):
            with self.subTest(size=len(raw)):
                api._opener = Opener(raw)
                with self.assertRaises(c.ClaimError):
                    api.issue_is_open(REPO, 478)


if __name__ == "__main__":
    unittest.main()
