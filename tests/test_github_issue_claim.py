"""No network or credentials: deterministic REST boundary tests."""
import importlib.util
from pathlib import Path
import unittest
from urllib.error import HTTPError, URLError

MODULE = Path(__file__).resolve().parents[1] / "tools" / "github_issue_claim.py"
spec = importlib.util.spec_from_file_location("github_issue_claim", MODULE)
mod = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

SHA = "a" * 40
OTHER = "b" * 40
REF = "refs/heads/agent-claims/issue-966"


class Reply:
    def __init__(self, code, body):
        self.status = code
        self.body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n=-1):
        data, self.body = self.body[:n], self.body[n:]
        return data


def success(ref=REF, sha=SHA, typ="commit"):
    return '{"ref":"%s","object":{"type":"%s","sha":"%s"}}' % (ref, typ, sha)


class ScriptedOpener:
    def __init__(self, *results):
        self.script = list(results)
        self.calls = []

    def open(self, req, timeout):
        self.calls.append(req)
        if timeout != 10:
            raise AssertionError("timeout missing")
        if not self.script:
            raise AssertionError("unexpected network request")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def http_error(code):
    return HTTPError("https://api.github.com/", code, "test", {}, None)


class ClaimTests(unittest.TestCase):
    def test_acquired_one_post_with_expected_ref_and_sha(self):
        api = ScriptedOpener(Reply(201, success()))
        result = mod.reserve_issue_ref("Garyu-tt/mojum", 966, SHA, "SECRET", opener=api)
        self.assertTrue(result.acquired)
        self.assertEqual(result.ref, REF)
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.calls[0].get_method(), "POST")
        self.assertEqual(api.calls[0].full_url, "https://api.github.com/repos/Garyu-tt/mojum/git/refs")
        self.assertEqual(api.calls[0].data, ('{"ref":"%s","sha":"%s"}' % (REF, SHA)).encode())
        self.assertEqual(api.calls[0].get_header("Authorization"), "Bearer SECRET")

    def test_conflict_is_busy_only_after_exact_ref_get(self):
        api = ScriptedOpener(http_error(422), Reply(200, success(sha=OTHER)))
        r = mod.reserve_issue_ref("Garyu-tt/mojum", 966, SHA, "T", opener=api)
        self.assertFalse(r.acquired)
        self.assertEqual(r.head_sha, OTHER)
        self.assertEqual([q.get_method() for q in api.calls], ["POST", "GET"])
        self.assertTrue(api.calls[1].full_url.endswith("/git/ref/heads/agent-claims/issue-966"))

    def test_conflict_when_ref_missing_is_not_busy(self):
        api = ScriptedOpener(http_error(422), http_error(404))
        with self.assertRaisesRegex(mod.ClaimError, "not verified"):
            mod.reserve_issue_ref("Garyu-tt/mojum", 966, SHA, "T", opener=api)

    def test_conflict_wrong_ref_is_rejected(self):
        api = ScriptedOpener(http_error(422), Reply(200, success(ref="refs/heads/unrelated")))
        with self.assertRaisesRegex(mod.ClaimError, "response mismatch"):
            mod.reserve_issue_ref("Garyu-tt/mojum", 966, SHA, "T", opener=api)

    def test_conflict_wrong_object_type_is_rejected(self):
        api = ScriptedOpener(http_error(422), Reply(200, success(typ="tag")))
        with self.assertRaises(mod.ClaimError):
            mod.reserve_issue_ref("Garyu-tt/mojum", 966, SHA, "T", opener=api)

    def test_non_conflict_http_failures_do_not_get(self):
        for code in (301, 302, 401, 403, 404, 409, 429, 500):
            with self.subTest(code=code):
                api = ScriptedOpener(http_error(code))
                with self.assertRaises(mod.ClaimError):
                    mod.reserve_issue_ref("Garyu-tt/mojum", 966, SHA, "T", opener=api)
                self.assertEqual(len(api.calls), 1)

    def test_malformed_and_oversized_json_rejected(self):
        for body in ('not json', '[]', '{}', success(sha='a'),
                     '{"ref":"x","ref":"y"}', '{"obj":NaN}',
                     '"x"' * 5000):
            with self.subTest(body=body[:30]):
                api = ScriptedOpener(Reply(201, body))
                with self.assertRaises(mod.ClaimError):
                    mod.reserve_issue_ref("Garyu-tt/mojum", 966, SHA, "T", opener=api)

    def test_new_ref_wrong_sha_does_not_claim_success(self):
        api = ScriptedOpener(Reply(201, success(sha=OTHER)))
        with self.assertRaisesRegex(mod.ClaimError, "unexpected commit"):
            mod.reserve_issue_ref("Garyu-tt/mojum", 966, SHA, "T", opener=api)

    def test_transport_error_never_mentions_secret(self):
        api = ScriptedOpener(URLError("SECRET private host"))
        with self.assertRaises(mod.ClaimError) as exc:
            mod.reserve_issue_ref("Garyu-tt/mojum", 966, SHA, "SECRET", opener=api)
        self.assertNotIn("SECRET", str(exc.exception))

    def test_invalid_arguments_fail_without_network(self):
        api = ScriptedOpener()
        bad = [
            ("Garyu-tt//mojum", 966, SHA, "T"),
            ("../mojum", 966, SHA, "T"),
            ("a/.git", 966, SHA, "T"),
            ("Garyu-tt/mojum", 0, SHA, "T"),
            ("Garyu-tt/mojum", True, SHA, "T"),
            ("Garyu-tt/mojum", 966, "main", "T"),
            ("Garyu-tt/mojum", 966, SHA, ""),
            ("Garyu-tt/mojum", 966, SHA, "T\nInjected: 1"),
            ("Garyu-tt/mojum", 966, SHA, "T\x00"),
            ("Garyu-tt/mojum", 966, SHA, "T\t"),
        ]
        for args in bad:
            with self.subTest(args=args):
                with self.assertRaises(mod.ClaimError):
                    mod.reserve_issue_ref(*args, opener=api)
        self.assertEqual(api.calls, [])

    def test_redirect_policy_never_follows(self):
        policy = mod._RejectRedirect()
        self.assertIsNone(policy.redirect_request(None, None, 302, "move", {}, "https://evil.invalid"))

    def test_201_non_success_status_fails_closed(self):
        for code in (200, 204, 207):
            with self.subTest(code=code):
                api = ScriptedOpener(Reply(code, success()))
                with self.assertRaises(mod.ClaimError):
                    mod.reserve_issue_ref("Garyu-tt/mojum", 966, SHA, "T", opener=api)


if __name__ == "__main__":
    unittest.main()
