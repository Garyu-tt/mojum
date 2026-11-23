"""Hermetic no-network tests for opt-in advisory issue claim audit."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "issue_claim_audit", Path(__file__).resolve().parents[1] / "tools" / "issue_claim_audit.py"
)
claim_audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(claim_audit)


def marker(worker, scope, action="claim"):
    payload = json.dumps({"worker": worker, "scope": scope, "action": action}, separators=(",", ":"))
    return f"<!-- mojum-claim/1: {payload} -->"


def comment(cid, author, text, at="2026-11-23T12:00:00Z"):
    return {"id": cid, "author": author, "created_at": at, "body": text}


def snapshot(*comments, issue=478):
    return {"schema": claim_audit.INPUT_SCHEMA, "repository": "Garyu-tt/mojum",
            "issues": [{"number": issue, "comments": list(comments)}]}


class ClaimAuditTests(unittest.TestCase):
    def test_detects_two_independent_workers_same_scope(self):
        report = claim_audit.audit(snapshot(
            comment(1, "bot", marker("agent-a", "issue-478/provider")),
            comment(2, "bot", marker("agent-b", "issue-478/provider")),
        ))
        self.assertEqual(report["status"], "collision")
        self.assertEqual(report["collision_scopes"], 1)
        self.assertEqual(report["issues"][0]["collisions"][0]["workers"], ["agent-a", "agent-b"])
        self.assertFalse(report["reservation_performed"])
        self.assertFalse(report["merge_authorized"])

    def test_two_scopes_on_umbrella_are_not_collisions(self):
        report = claim_audit.audit(snapshot(
            comment(1, "bot", marker("agent-a", "issue-478/provider")),
            comment(2, "bot", marker("agent-b", "issue-478/reviewer")),
        ))
        self.assertEqual(report["status"], "clear")
        self.assertEqual(len(report["issues"][0]["active_claims"]), 2)

    def test_authorized_release_clears_prior_claim(self):
        report = claim_audit.audit(snapshot(
            comment(2, "bot", marker("agent-b", "scope-a"), "2026-11-23T12:00:01Z"),
            comment(3, "bot", marker("agent-a", "scope-a", "release"), "2026-11-23T12:00:02Z"),
            comment(1, "bot", marker("agent-a", "scope-a"), "2026-11-23T12:00:00Z"),
        ))
        self.assertEqual(report["status"], "clear")
        self.assertEqual([x["worker"] for x in report["issues"][0]["active_claims"]], ["agent-b"])

    def test_release_by_different_actor_does_not_silence_collision(self):
        report = claim_audit.audit(snapshot(
            comment(1, "original", marker("agent-a", "scope-a")),
            comment(2, "other", marker("agent-a", "scope-a", "release"), "2026-11-23T12:00:01Z"),
            comment(3, "other", marker("agent-b", "scope-a"), "2026-11-23T12:00:02Z"),
        ))
        self.assertEqual(report["status"], "collision")
        self.assertEqual(report["issues"][0]["manual_review"][0]["reason"], "RELEASE_ACTOR_MISMATCH")

    def test_unknown_legacy_claim_requires_manual_review(self):
        report = claim_audit.audit(snapshot(comment(1, "bot", "🔒 CLAIMED / IN PROGRESS; scope text")))
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["manual_review_sources"], 1)

    def test_issue_body_is_a_claim_source(self):
        raw = snapshot()
        raw["issues"][0].update({"body": marker("agent-a", "scope-a"), "author": "bot",
                                 "created_at": "2026-11-23T11:00:00Z"})
        report = claim_audit.audit(raw)
        self.assertEqual(report["issues"][0]["active_claims"][0]["source_id"], 0)

    def test_duplicate_claim_by_same_worker_is_idempotent(self):
        raw = snapshot(comment(1, "bot", marker("agent-a", "scope-a")),
                       comment(2, "bot", marker("agent-a", "scope-a")))
        report = claim_audit.audit(raw)
        self.assertEqual(report["status"], "clear")
        self.assertEqual(len(report["issues"][0]["active_claims"]), 1)

    def test_wrong_actor_same_worker_must_be_flagged(self):
        report = claim_audit.audit(snapshot(
            comment(1, "bot-a", marker("agent-a", "scope-a")),
            comment(2, "bot-b", marker("agent-a", "scope-a"))))
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["manual_review_sources"], 1)

    def test_same_scope_different_issues_is_allowed(self):
        raw = snapshot(comment(1, "bot", marker("agent-a", "base")))
        raw["issues"].append({"number": 479, "comments": [comment(2, "bot", marker("agent-b", "base"))]})
        self.assertEqual(claim_audit.audit(raw)["status"], "clear")

    def test_rejects_malformed_markers_ambiguity(self):
        candidates = [
            "<!-- mojum-claim/1: not-json -->",
            marker("agent", "scope") + "\n" + marker("other", "scope"),
            '<!-- mojum-claim/1: {"worker":"x","worker":"y","scope":"x","action":"claim"} -->',
            '<!-- mojum-claim/1: {"worker":"x","scope":"x","action":"claim","extra":1} -->',
            '> ' + marker("agent", "scope"),
            marker("agent", "scope") + "\n<!-- mojum-claim/1: broken -->",
        ]
        for raw in candidates:
            with self.subTest(raw=raw), self.assertRaises(claim_audit.ClaimAuditError):
                claim_audit.audit(snapshot(comment(1, "bot", raw)))

    def test_rejects_duplicate_json_issue_number_comment_id_and_bool_id(self):
        for raw in [
            {"schema": claim_audit.INPUT_SCHEMA, "repository": "x/y", "issues": [
                {"number": 1, "comments": []}, {"number": 1, "comments": []}]},
            snapshot(comment(1, "bot", ""), comment(1, "bot", "")),
            snapshot(issue=True),
        ]:
            with self.subTest(raw=raw), self.assertRaises(claim_audit.ClaimAuditError):
                claim_audit.audit(raw)

    def test_rejects_non_utc_timestamp_and_missing_author(self):
        for raw in [snapshot(comment(1, "bot", marker("agent", "scope"), "2026-11-23T12:00:00+03:00")),
                    snapshot({"id": 1, "created_at": "2026-11-23T12:00:00Z", "body": ""})]:
            with self.assertRaises(claim_audit.ClaimAuditError):
                claim_audit.audit(raw)

    def test_code_fenced_and_indented_example_markers_fail_closed(self):
        real = marker("agent-a", "issue-786/audit")
        examples = [
            "```text\n" + real + "\n```",
            "~~~markdown\n" + real + "\n~~~",
            "    " + real,
            "\t" + real,
        ]
        for body in examples:
            with self.subTest(body=body), self.assertRaises(claim_audit.ClaimAuditError):
                claim_audit.audit(snapshot(comment(1, "docs", body)))

    def test_marker_after_closed_fence_is_accepted(self):
        raw = "Example:\n```text\nnot a claim\n```\n" + marker("agent-a", "scope-a")
        report = claim_audit.audit(snapshot(comment(1, "bot", raw)))
        self.assertEqual(report["status"], "clear")
        self.assertEqual(report["issues"][0]["active_claims"][0]["worker"], "agent-a")

    def test_reject_global_duplicate_comment_ids(self):
        raw = snapshot(comment(1, "bot", marker("agent-a", "scope-a")))
        raw["issues"].append({"number": 479, "comments": [comment(1, "bot", marker("agent-b", "scope-b"))]})
        with self.assertRaisesRegex(claim_audit.ClaimAuditError, "duplicate comment id"):
            claim_audit.audit(raw)

    def test_surrogate_body_is_typed_invalid(self):
        with self.assertRaisesRegex(claim_audit.ClaimAuditError, "valid UTF-8"):
            claim_audit.audit(snapshot(comment(1, "bot", "\ud800")))

    def test_cli_exit_status_collision_and_corrupt_json(self):
        script = Path(__file__).resolve().parents[1] / "tools" / "issue_claim_audit.py"
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "export.json"
            target.write_text(json.dumps(snapshot(
                comment(1, "bot", marker("a", "scope")),
                comment(2, "bot", marker("b", "scope")))), encoding="utf-8")
            proc = subprocess.run([sys.executable, str(script), "--input", str(target)],
                                  capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["status"], "collision")
            target.write_text('{"schema": "x", "schema": "x"}', encoding="utf-8")
            proc = subprocess.run([sys.executable, str(script), "--input", str(target)],
                                  capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
