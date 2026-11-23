import unittest
from unittest.mock import Mock, patch

from mojum import qa_workflow_evidence as evidence
from mojum.qa_workflow_evidence import WorkflowEvidenceError, evaluate_workflow_evidence

HEAD = "a" * 40
OTHER = "b" * 40

def run(run_id=1, workflow_id=10, *, sha=HEAD, attempt=1, status="completed", conclusion="success", name="unit"):
    return {"run_id": run_id, "run_attempt": attempt, "workflow_id": workflow_id, "name": name,
            "head_sha": sha, "event": "pull_request", "status": status, "conclusion": conclusion}

class WorkflowEvidenceTests(unittest.TestCase):
    def test_exact_head_matrix_passes_deterministically(self):
        runs = [run(2, 20, name="integration"), run(1, 10)]
        a = evaluate_workflow_evidence(runs, expected_head_sha=HEAD, required_workflow_ids=[20, 10])
        b = evaluate_workflow_evidence(list(reversed(runs)), expected_head_sha=HEAD, required_workflow_ids=[10, 20])
        self.assertEqual(a.state, "PASS")
        self.assertEqual(a, b)
        self.assertEqual([x.workflow_id for x in a.results], [10, 20])

    def test_empty_and_stale_evidence_hold(self):
        empty = evaluate_workflow_evidence([], expected_head_sha=HEAD, required_workflow_ids=[10])
        stale = evaluate_workflow_evidence([run(sha=OTHER)], expected_head_sha=HEAD, required_workflow_ids=[10])
        self.assertEqual(empty.results[0].state, "MISSING")
        self.assertEqual(stale.results[0].state, "STALE")
        self.assertEqual(stale.stale_run_count, 1)
        self.assertEqual(empty.state, "HOLD")

    def test_newer_stale_head_run_supersedes_exact_head_success(self):
        result = evaluate_workflow_evidence(
            [run(1, sha=HEAD), run(2, sha=OTHER, status="in_progress", conclusion=None)],
            expected_head_sha=HEAD, required_workflow_ids=[10],
        )
        self.assertEqual(result.results[0].state, "STALE")
        self.assertEqual(result.results[0].run_id, 2)
        self.assertEqual(result.stale_run_count, 1)
        self.assertEqual(result.state, "HOLD")

    def test_latest_pending_attempt_supersedes_success(self):
        result = evaluate_workflow_evidence([run(1, attempt=1), run(2, attempt=2, status="in_progress", conclusion=None)],
            expected_head_sha=HEAD, required_workflow_ids=[10])
        self.assertEqual(result.results[0].state, "PENDING")
        self.assertEqual(result.state, "HOLD")

    def test_newer_run_supersedes_older_run_with_more_attempts(self):
        result = evaluate_workflow_evidence(
            [run(100, attempt=2, conclusion="failure"), run(101, attempt=1, conclusion="success")],
            expected_head_sha=HEAD, required_workflow_ids=[10],
        )
        self.assertEqual(result.results[0].run_id, 101)
        self.assertEqual(result.state, "PASS")

    def test_neutral_and_skipped_conclusions_hold(self):
        for conclusion in ("neutral", "skipped"):
            with self.subTest(conclusion=conclusion):
                result = evaluate_workflow_evidence(
                    [run(conclusion=conclusion)],
                    expected_head_sha=HEAD, required_workflow_ids=[10],
                )
                self.assertEqual(result.results[0].state, "FAILURE")
                self.assertEqual(result.state, "HOLD")

    def test_failures_hold(self):
        for conclusion in ("failure", "cancelled", "timed_out", "action_required"):
            with self.subTest(conclusion=conclusion):
                result = evaluate_workflow_evidence([run(conclusion=conclusion)], expected_head_sha=HEAD, required_workflow_ids=[10])
                self.assertEqual(result.results[0].state, "FAILURE")

    def test_non_required_workflow_does_not_count(self):
        result = evaluate_workflow_evidence([run(workflow_id=99)], expected_head_sha=HEAD, required_workflow_ids=[10])
        self.assertEqual(result.results[0].state, "MISSING")

    def test_duplicate_run_and_requirement_rejected(self):
        with self.assertRaises(WorkflowEvidenceError):
            evaluate_workflow_evidence([run(), run()], expected_head_sha=HEAD, required_workflow_ids=[10])
        with self.assertRaises(WorkflowEvidenceError):
            evaluate_workflow_evidence([run()], expected_head_sha=HEAD, required_workflow_ids=[10, 10])

    def test_hostile_key_and_unknown_field_rejected(self):
        class HostileKey(str):
            armed = False
            def __hash__(self):
                if self.armed: raise AssertionError("hostile hash called")
                return str.__hash__(self)
        key = HostileKey("run_id")
        item = run()
        item.pop("run_id")
        item[key] = 1
        key.armed = True
        with self.assertRaises(WorkflowEvidenceError):
            evaluate_workflow_evidence([item], expected_head_sha=HEAD, required_workflow_ids=[10])
        with self.assertRaises(WorkflowEvidenceError):
            evaluate_workflow_evidence([dict(run(), token="secret")], expected_head_sha=HEAD, required_workflow_ids=[10])

    def test_bool_integer_rejected(self):
        item = run()
        item["run_id"] = True
        with self.assertRaises(WorkflowEvidenceError):
            evaluate_workflow_evidence([item], expected_head_sha=HEAD, required_workflow_ids=[10])
        with self.assertRaises(WorkflowEvidenceError):
            evaluate_workflow_evidence([run()], expected_head_sha=HEAD, required_workflow_ids=[True])

    def test_malformed_sha_event_status_and_conclusion_rejected(self):
        for change in ({"head_sha": "bad"}, {"event": "push"}, {"status": "success"}, {"status": "completed", "conclusion": "not-a-github-conclusion"}):
            with self.subTest(change=change), self.assertRaises(WorkflowEvidenceError):
                evaluate_workflow_evidence([dict(run(), **change)], expected_head_sha=HEAD, required_workflow_ids=[10])

    def test_oversized_sha_rejected_before_regex(self):
        oversized = "a" * 1_000_000
        matcher = Mock()
        with patch.object(evidence, "_SHA", matcher):
            with self.assertRaises(WorkflowEvidenceError):
                evaluate_workflow_evidence([], expected_head_sha=oversized, required_workflow_ids=[10])
            matcher.fullmatch.assert_not_called()
            matcher.fullmatch.reset_mock()
            with self.assertRaises(WorkflowEvidenceError):
                evaluate_workflow_evidence([run(sha=oversized)], expected_head_sha=HEAD, required_workflow_ids=[10])
            matcher.fullmatch.assert_called_once_with(HEAD)

    def test_oversized_integer_ids_rejected(self):
        huge = 1 << 1_000_000
        for field in ("run_id", "run_attempt", "workflow_id"):
            with self.subTest(field=field):
                item = run()
                item[field] = huge
                with self.assertRaises(WorkflowEvidenceError):
                    evaluate_workflow_evidence([item], expected_head_sha=HEAD, required_workflow_ids=[10])
        with self.assertRaises(WorkflowEvidenceError):
            evaluate_workflow_evidence([run()], expected_head_sha=HEAD, required_workflow_ids=[huge])

    def test_input_unchanged_and_digest_stable(self):
        item = run()
        before = dict(item)
        a = evaluate_workflow_evidence([item], expected_head_sha=HEAD, required_workflow_ids=[10])
        b = evaluate_workflow_evidence([item], expected_head_sha=HEAD, required_workflow_ids=[10])
        self.assertEqual(item, before)
        self.assertEqual(a.digest, b.digest)
        self.assertEqual(len(a.digest), 64)

    def test_bounds(self):
        with self.assertRaises(WorkflowEvidenceError):
            evaluate_workflow_evidence([run()] * 501, expected_head_sha=HEAD, required_workflow_ids=[10])
        with self.assertRaises(WorkflowEvidenceError):
            evaluate_workflow_evidence([run()], expected_head_sha=HEAD, required_workflow_ids=list(range(1, 66)))

if __name__ == "__main__":
    unittest.main()
