"""Hermetic PR-queue triage checks; no GitHub credentials or network."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "tools" / "pr_queue_triage.py"
spec = importlib.util.spec_from_file_location("pr_queue_triage", SOURCE)
triage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(triage)

HEAD = "a" * 40
BASE = "b" * 40
OTHER = "c" * 40
JOBS = ("Aquarium / py311", "Aquarium / py312", "Aquarium / py313")


def check(job, conclusion="SUCCESS", status="COMPLETED", head=HEAD):
    workflow, name = job.split(" / ")
    return {"workflowName": workflow, "name": name,
            "status": status, "conclusion": conclusion, "headSha": head}


def pr(number=1, checks=None, **changed):
    payload = {"number": number, "headRefOid": HEAD, "baseRefOid": BASE,
               "baseRefName": "main", "isDraft": False,
               "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
               "statusCheckRollup": [check(j) for j in JOBS] if checks is None else checks}
    payload.update(changed)
    return payload


def run(*prs, required=JOBS):
    return triage.scan_queue({"base_sha": BASE, "pull_requests": list(prs)}, required)


class QueueTriageTests(unittest.TestCase):
    def test_complete_matrix_is_only_candidate_not_approved(self):
        report = run(pr(3), pr(1))
        self.assertEqual([x["number"] for x in report["pull_requests"]], [1, 3])
        self.assertEqual(report["counts"], {"candidate_for_independent_acceptance": 2})
        self.assertFalse(report["merge_authorized"])
        self.assertFalse(report["merge_performed"])

    def test_one_green_matrix_job_cannot_certify_three(self):
        result = run(pr(checks=[check(JOBS[0])]))["pull_requests"][0]
        self.assertEqual(result["status"], "defer")
        self.assertIn("MISSING_REQUIRED_CHECK: Aquarium / py312", result["reasons"])
        self.assertIn("MISSING_REQUIRED_CHECK: Aquarium / py313", result["reasons"])

    def test_failed_job_wins_over_missing_sibling(self):
        result = run(pr(checks=[check(JOBS[0], "FAILURE")]))["pull_requests"][0]
        self.assertEqual(result["status"], "blocked")
        self.assertIn("NON_SUCCESS_CHECK: Aquarium / py311 (FAILURE)", result["reasons"])

    def test_pending_with_failure_conclusion_fails_closed(self):
        result = run(pr(checks=[check(JOBS[0], "FAILURE", "IN_PROGRESS")]))["pull_requests"][0]
        self.assertEqual(result["status"], "blocked")

    def test_neutral_skipped_and_unrecognized_conclusions_cannot_pass(self):
        for conclusion in ("NEUTRAL", "SKIPPED", "ALIEN"):
            with self.subTest(conclusion=conclusion):
                result = run(pr(checks=[check(JOBS[0], conclusion)]))["pull_requests"][0]
                self.assertNotEqual(result["status"], "candidate_for_independent_acceptance")

    def test_duplicate_check_labels_defer(self):
        result = run(pr(checks=[check(JOBS[0]), check(JOBS[0])]))["pull_requests"][0]
        self.assertEqual(result["status"], "defer")
        self.assertIn("AMBIGUOUS_CHECK_LABEL: Aquarium / py311", result["reasons"])

    def test_draft_nonclean_and_wrong_base_cannot_pass(self):
        for change in ({"isDraft": True}, {"mergeStateStatus": "BEHIND"},
                       {"baseRefOid": OTHER}, {"baseRefName": "dev"},
                       {"mergeable": "CONFLICTING"}):
            with self.subTest(change=change):
                result = run(pr(**change))["pull_requests"][0]
                self.assertEqual(result["status"], "defer")

    def test_unbound_check_run_shas_are_audited(self):
        result = run(pr(checks=[check(JOBS[0], head=None),
                                check(JOBS[1], head=OTHER), check(JOBS[2])]))["pull_requests"][0]
        self.assertEqual(result["status"], "candidate_for_independent_acceptance")
        self.assertEqual(result["unverified_check_heads"], [JOBS[0], JOBS[1]])
        self.assertFalse(run(pr())["merge_authorized"])

    def test_malformed_pr_does_not_stop_other_prs(self):
        report = run(pr(2), {"number": 1, "headRefOid": "bad"}, pr(3))
        self.assertEqual(report["counts"]["invalid"], 1)
        self.assertEqual(report["counts"]["candidate_for_independent_acceptance"], 2)

    def test_duplicate_pr_numbers_invalidate_all_copies(self):
        report = run(pr(7), pr(7))
        self.assertEqual(report["counts"], {"invalid": 2})
        self.assertTrue(all("DUPLICATE_PR_NUMBER" in x["reasons"]
                            for x in report["pull_requests"]))

    def test_wildcard_empty_and_duplicate_requirements_rejected(self):
        for bad in ([], ["Aquarium*"], ["Aquarium / py311"] * 2,
                    [""], [" Aquarium / py311"]):
            with self.subTest(bad=bad):
                with self.assertRaises(triage.TriageInputError):
                    run(pr(), required=bad)

    def test_invalid_rollup_and_extra_untrusted_fields(self):
        result = run(pr(statusCheckRollup="fake"))["pull_requests"][0]
        self.assertEqual(result["status"], "invalid")
        result = run(pr(checks=[{"status": "SUCCESS"}]))["pull_requests"][0]
        self.assertEqual(result["status"], "invalid")

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(triage.TriageInputError):
            triage.scan_queue({"base_sha": "x", "pull_requests": []}, JOBS)
        with self.assertRaises(triage.TriageInputError):
            triage.scan_queue({"base_sha": BASE, "pull_requests": {}}, JOBS)

    def test_orphan_success_conclusion_is_not_a_completed_check(self):
        forged = [check(job) for job in JOBS]
        del forged[0]["status"]
        result = run(pr(checks=forged))["pull_requests"][0]
        self.assertEqual(result["status"], "invalid")
        self.assertIn("MALFORMED_CHECK_ENTRY", result["reasons"])

    def test_mixed_status_context_cannot_override_failed_check(self):
        forged = [check(job) for job in JOBS]
        forged[0].update(state="SUCCESS", conclusion="FAILURE")
        result = run(pr(checks=forged))["pull_requests"][0]
        self.assertEqual(result["status"], "invalid")
        self.assertIn("MALFORMED_CHECK_ENTRY", result["reasons"])
        legacy = [dict(workflowName="Aquarium", name="py311", state="SUCCESS", headSha=HEAD)]
        legacy += [check(job) for job in JOBS[1:]]
        self.assertEqual(run(pr(checks=legacy))["pull_requests"][0]["status"],
                         "candidate_for_independent_acceptance")

    def test_cli_rejects_duplicate_json_keys_and_nonfinite_numbers(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "snapshot.json"
            good = json.dumps({"base_sha": BASE, "pull_requests": [pr()]})
            for corrupted in (
                good.replace('"isDraft": false', '"isDraft": true, "isDraft": false'),
                good.replace('"number": 1', '"number": NaN'),
                "[" * 1500 + "]" * 1500,
            ):
                with self.subTest(corrupted=corrupted[:50]):
                    filename.write_text(corrupted, encoding="utf-8")
                    with contextlib.redirect_stdout(io.StringIO()) as out:
                        code = triage.main(["--input", str(filename), "--require", JOBS[0]])
                    self.assertEqual(code, 2)
                    report = json.loads(out.getvalue())
                    self.assertFalse(report["merge_authorized"])
                    self.assertFalse(report["merge_performed"])

    def test_cli_exit_zero_only_reports_not_merges(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "snapshot.json"
            filename.write_text(json.dumps({"base_sha": BASE, "pull_requests": [pr()]}))
            import contextlib
            import io
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = triage.main(["--input", str(filename), "--require", JOBS[0],
                                    "--require", JOBS[1], "--require", JOBS[2]])
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(out.getvalue())["merge_authorized"])

    def test_cli_rejects_oversize_input(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "snapshot.json"
            filename.write_bytes(b" " * (triage.MAX_INPUT_BYTES + 1))
            import contextlib
            import io
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = triage.main(["--input", str(filename), "--require", JOBS[0]])
            self.assertEqual(code, 2)
            self.assertFalse(json.loads(out.getvalue())["merge_authorized"])


if __name__ == "__main__":
    unittest.main()
