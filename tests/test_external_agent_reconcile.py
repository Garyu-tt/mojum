"""Hermetic acceptance checks for offline external worker/GitHub correlation."""
import copy
import unittest

from mojum.external_agent_reconcile import (
    CorrelationError, GITHUB_SCHEMA, SCHEMA, reconcile,
)

SHA = "a" * 40
REPO = "Garyu-tt/mojum"


def run(agent="worker1", run_id="run1", *, state="active", kind="agent.heartbeat",
        age=100, pr=42, issue=12, sha=SHA, source="chatgpt"):
    return {
        "source": source, "agent_id": agent, "run_id": run_id,
        "state": state, "kind": kind, "seq": 2,
        "observed_ms": 1_000_000 - age, "age_ms": age, "ttl_ms": 10_000,
        "repo": REPO, "host": "host01", "provider": "test-provider",
        "issue": issue, "pr": pr, "sha": sha,
        "status_code": "completed" if kind == "agent.finished" else (
            "failed" if kind == "agent.failed" else "none"),
    }


def readback(*runs, truncated=False):
    return {"observed_ms": 1_000_000, "truncated": truncated, "runs": list(runs)}


def github(*, age=100, merged=False, sha=SHA, repo=REPO):
    return {"schema": GITHUB_SCHEMA, "repo": repo,
            "observed_ms": 1_000_000-age, "complete": False,
            "issues": [{"number": 12, "state": "open"}],
            "pulls": [{"number": 42, "state": "closed" if merged else "open",
                       "merged": merged, "head_sha": sha}]}


class OfflineCorrelationTests(unittest.TestCase):
    def test_verified_active_with_matching_pr_and_issue(self):
        actual = reconcile(readback(run()), github())
        self.assertEqual(actual["schema"], SCHEMA)
        self.assertEqual(actual["counts"]["active"], 1)
        self.assertEqual(actual["runs"][0]["github"]["sha_match"], True)
        self.assertEqual(actual["runs"][0]["github"]["pr"]["url"],
                         "https://github.com/Garyu-tt/mojum/pull/42")

    def test_stale_cannot_be_resuscitated_by_open_pr(self):
        r = run(state="stale", age=10_000)
        actual = reconcile(readback(r), github())
        self.assertEqual(actual["runs"][0]["state"], "stale")
        self.assertEqual(actual["counts"]["active"], 0)

    def test_merged_pr_does_not_terminate_live_worker(self):
        actual = reconcile(readback(run()), github(merged=True))
        r = actual["runs"][0]
        self.assertEqual(r["state"], "active")
        self.assertEqual(r["github"]["pr"]["state"], "merged")
        self.assertIn("merged_pr_does_not_finish_active_run", r["github"]["flags"])

    def test_finished_run_is_not_resuscitated(self):
        actual = reconcile(readback(run(state="finished", kind="agent.finished")), github())
        self.assertEqual(actual["counts"]["finished"], 1)
        self.assertEqual(actual["counts"]["active"], 0)

    def test_failed_run_persists_even_if_pr_open(self):
        actual = reconcile(readback(run(state="failed", kind="agent.failed")), github())
        self.assertEqual(actual["runs"][0]["state"], "failed")

    def test_schedule_only_is_unknown_not_zero_or_active(self):
        schedules = [{"source":"chatgpt", "agent_id":"agent9", "repo":REPO}]
        actual = reconcile(readback(), github(), schedules=schedules)
        self.assertEqual(actual["counts"]["active"], 0)
        self.assertEqual(actual["schedule_only"][0]["state"], "unknown")
        self.assertEqual(actual["counts"]["schedule_only_unknown"], 1)

    def test_schedule_for_observed_agent_does_not_duplicate(self):
        schedules = [{"source":"chatgpt", "agent_id":"worker1", "repo":REPO}]
        actual = reconcile(readback(run()), github(), schedules=schedules)
        self.assertEqual(actual["schedule_only"], [])
        self.assertTrue(actual["runs"][0]["scheduled"])

    def test_github_missing_cannot_synthesize_live_workers(self):
        actual = reconcile(readback(), None)
        self.assertEqual(actual["github_freshness"], "unavailable")
        self.assertEqual(actual["runs"], [])

    def test_stale_github_is_not_current_evidence(self):
        actual = reconcile(readback(run()), github(age=300_001))
        self.assertEqual(actual["github_freshness"], "stale")
        self.assertIsNone(actual["runs"][0]["github"]["pr"])
        self.assertEqual(actual["runs"][0]["state"], "active")

    def test_github_repo_mismatch_not_correlated(self):
        actual = reconcile(readback(run()), github(repo="other/repo"))
        self.assertIsNone(actual["runs"][0]["github"]["issue"])
        self.assertIsNone(actual["runs"][0]["github"]["pr"])

    def test_mismatched_sha_is_flagged_not_silenced(self):
        actual = reconcile(readback(run()), github(sha="b"*40))
        self.assertFalse(actual["runs"][0]["github"]["sha_match"])
        self.assertEqual(actual["runs"][0]["github"]["flags"],
                         ["receipt_sha_differs_from_pr_head"])

    def test_partial_readback_explicit(self):
        actual = reconcile(readback(run(), truncated=True), github())
        self.assertTrue(actual["receipt_view_truncated"])
        self.assertEqual(actual["counts"]["active"], 1)

    def test_absent_github_number_is_unknown_not_closed(self):
        r = run(pr=999, issue=888)
        actual = reconcile(readback(r), github())
        self.assertIsNone(actual["runs"][0]["github"]["pr"])
        self.assertIsNone(actual["runs"][0]["github"]["issue"])

    def test_multiple_workers_not_bounded_by_cezar_parallel_setting(self):
        workers = [run(agent=f"w{i}", run_id=f"r{i}") for i in range(8)]
        actual = reconcile(readback(*workers), github())
        self.assertEqual(actual["counts"]["active"], 8)

    def test_sorting_is_stable(self):
        items = [run(agent="z", run_id="2"), run(agent="a", run_id="1")]
        a = reconcile(readback(*items), github())
        b = reconcile(readback(*reversed(items)), github())
        self.assertEqual(a, b)

    def assert_bad_run(self, field, value):
        r = run()
        r[field] = value
        with self.assertRaises(CorrelationError):
            reconcile(readback(r), github())

    def test_reject_bool_numbers_forged_age_terminal_and_fields(self):
        self.assert_bad_run("seq", True)
        self.assert_bad_run("age_ms", 101)
        self.assert_bad_run("state", "finished")
        self.assert_bad_run("ttl_ms", 0)
        self.assert_bad_run("repo", "../../etc/passwd")
        self.assert_bad_run("sha", "BAD")
        self.assert_bad_run("issue", True)
        self.assert_bad_run("pr", -1)
        self.assert_bad_run("status_code", "a secret exception")
        r = run(); r["secret"] = "bad"
        with self.assertRaises(CorrelationError):
            reconcile(readback(r))

    def test_reject_duplicate_run(self):
        with self.assertRaisesRegex(CorrelationError, "duplicate authenticated run"):
            reconcile(readback(run(), run()))

    def test_reject_malformed_readback_and_truncation(self):
        for value in ([], {}, {"observed_ms": True, "truncated":False,"runs":[]},
                      {"observed_ms":1000000,"truncated":0,"runs":[]}):
            with self.subTest(value=value), self.assertRaises(CorrelationError):
                reconcile(value)

    def test_reject_malicious_github_shapes(self):
        bad = []
        q=github(); q["pulls"][0]["merged"] = True; bad.append(q)
        q=github(); q["pulls"][0]["head_sha"] = "http://evil"; bad.append(q)
        q=github(); q["observed_ms"] = 1_000_001; bad.append(q)
        q=github(); q["issues"].append(copy.deepcopy(q["issues"][0])); bad.append(q)
        q=github(); q["pulls"].append(copy.deepcopy(q["pulls"][0])); bad.append(q)
        q=github(); q["repo"]="https://evil.test/repo"; bad.append(q)
        q=github(); q["complete"]=1; bad.append(q)
        q=github(); q["token"]="secret"; bad.append(q)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(CorrelationError):
                reconcile(readback(run()), value)

    def test_duplicate_schedule_is_rejected(self):
        s = {"source":"chatgpt", "agent_id":"foo", "repo":REPO}
        with self.assertRaisesRegex(CorrelationError, "duplicate schedule"):
            reconcile(readback(), schedules=[s,s])

    def test_collection_bounds(self):
        with self.assertRaises(CorrelationError):
            reconcile(readback(), schedules=[{}]*1001)
        q=github(); q["issues"] = []
        with self.assertRaises(CorrelationError):
            q["pulls"] = [{}] * 2001
            reconcile(readback(), q)
        with self.assertRaises(CorrelationError):
            reconcile(readback(*([run()]*1001)))

    def test_output_contains_no_host_provider_or_receipt_signature(self):
        actual = reconcile(readback(run()), github())
        self.assertNotIn("host01", str(actual))
        self.assertNotIn("test-provider", str(actual))
        self.assertNotIn("signature", str(actual))


if __name__ == "__main__":
    unittest.main()
