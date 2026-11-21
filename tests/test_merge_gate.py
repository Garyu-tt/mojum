"""Exact-head CI merge gate: verdicts, parsing and CLI boundaries (issue #415)."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from mojum.merge_gate import (
    EXIT_DEFER,
    EXIT_READY,
    EXIT_REJECT,
    EXIT_USAGE,
    SCHEMA,
    CheckOutcome,
    FetchError,
    PullRequestFacts,
    SnapshotError,
    evaluate,
    fetch_snapshot,
    main,
    parse_snapshot,
)

HEAD = "a" * 40
OTHER = "b" * 40
REQUIRED = ("Aquarium regression QA / test", "Motor Gate / motor", "Mesh contract / mesh")


def run(status="COMPLETED", conclusion="SUCCESS", name="Aquarium QA", workflow=None, head_sha=None):
    entry = {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "workflowName": workflow,
    }
    if head_sha is not None:
        entry["headSha"] = head_sha
    return entry


def facts(checks, **overrides):
    defaults = dict(
        head_sha=HEAD,
        base_ref="main",
        checks=tuple(checks),
        number=419,
        is_draft=False,
        mergeable="MERGEABLE",
        merge_state_status="CLEAN",
    )
    defaults.update(overrides)
    return PullRequestFacts(**defaults)


def evaluate_checks(checks, **overrides):
    fact_fields = {"head_sha", "base_ref", "is_draft", "mergeable", "merge_state_status"}
    fact_overrides = {k: v for k, v in overrides.items() if k in fact_fields}
    kwargs = {k: v for k, v in overrides.items() if k not in fact_fields}
    kwargs.setdefault("expected_head_sha", HEAD)
    kwargs.setdefault("expected_base", "main")
    kwargs.setdefault("required", REQUIRED)
    parsed = parse_snapshot(
        {
            "headRefOid": HEAD,
            "baseRefName": "main",
            "checks": list(checks),
        }
    )
    return evaluate(facts(parsed.checks, **fact_overrides), **kwargs)


class SnapshotParsingTests(unittest.TestCase):
    def test_gh_camel_case_rollup_with_check_run_and_status_context(self):
        snapshot = {
            "number": 415,
            "headRefOid": HEAD,
            "baseRefName": "main",
            "headRefName": "feature/x",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [
                run(name="aquarium", workflow="Aquarium regression QA"),
                {
                    "__typename": "StatusContext",
                    "context": "legacy/gate",
                    "state": "SUCCESS",
                    "targetUrl": "https://example.invalid",
                },
            ],
        }
        parsed = parse_snapshot(snapshot)
        self.assertEqual(parsed.head_sha, HEAD)
        self.assertEqual(parsed.base_ref, "main")
        self.assertEqual(parsed.number, 415)
        self.assertEqual(parsed.head_ref, "feature/x")
        self.assertEqual([c.name for c in parsed.checks], ["aquarium", "legacy/gate"])
        self.assertEqual(parsed.checks[0].workflow, "Aquarium regression QA")
        self.assertEqual(parsed.checks[1].status, "COMPLETED")
        self.assertEqual(parsed.checks[1].conclusion, "SUCCESS")

    def test_snake_case_fixture_schema(self):
        snapshot = {
            "number": 1,
            "head_sha": HEAD,
            "base_ref": "main",
            "is_draft": True,
            "mergeable": "MERGEABLE",
            "merge_state_status": "DRAFT",
            "checks": [{"name": "Mesh contract", "status": "queued"}],
        }
        parsed = parse_snapshot(snapshot)
        self.assertTrue(parsed.is_draft)
        self.assertEqual(parsed.checks[0].status, "PENDING")

    def test_status_context_pending_and_failure_states(self):
        for state, status, conclusion in [
            ("EXPECTED", "PENDING", None),
            ("PENDING", "PENDING", None),
            ("FAILURE", "COMPLETED", "FAILURE"),
            ("ERROR", "COMPLETED", "ERROR"),
        ]:
            with self.subTest(state=state):
                parsed = parse_snapshot(
                    {
                        "headRefOid": HEAD,
                        "baseRefName": "main",
                        "statusCheckRollup": [
                            {"__typename": "StatusContext", "context": "ctx", "state": state}
                        ],
                    }
                )
                self.assertEqual(parsed.checks[0].status, status)
                self.assertEqual(parsed.checks[0].conclusion, conclusion)

    def test_invalid_snapshots_are_rejected(self):
        cases = [
            [],
            {"baseRefName": "main"},
            {"headRefOid": "not-a-sha", "baseRefName": "main"},
            {"headRefOid": HEAD},
            {"headRefOid": HEAD, "baseRefName": "main", "statusCheckRollup": "nope"},
            {"headRefOid": HEAD, "baseRefName": "main", "statusCheckRollup": [{}]},
            {
                "headRefOid": HEAD,
                "baseRefName": "main",
                "isDraft": "yes",
            },
            {
                "headRefOid": HEAD,
                "baseRefName": "main",
                "number": "419",
            },
        ]
        for snapshot in cases:
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(SnapshotError):
                    parse_snapshot(snapshot)

    def test_snapshot_rejects_malformed_head_object_ids(self):
        for oid in (HEAD[:7], HEAD[:39], HEAD + "f", HEAD + "\n", " " + HEAD):
            with self.subTest(oid=oid):
                with self.assertRaises(SnapshotError):
                    parse_snapshot({"headRefOid": oid, "baseRefName": "main"})
                with self.assertRaises(SnapshotError):
                    parse_snapshot({
                        "headRefOid": HEAD,
                        "baseRefName": "main",
                        "checks": [run(head_sha=oid)],
                    })

    def test_snapshot_accepts_full_sha256_object_id(self):
        oid = "f" * 64
        parsed = parse_snapshot({
            "headRefOid": oid,
            "baseRefName": "main",
            "checks": [run(head_sha=oid)],
        })
        self.assertEqual(parsed.head_sha, oid)
        self.assertEqual(parsed.checks[0].head_sha, oid)

    def test_check_without_status_but_with_conclusion_is_completed(self):
        parsed = parse_snapshot(
            {
                "headRefOid": HEAD,
                "baseRefName": "main",
                "checks": [{"name": "gate", "conclusion": "success"}],
            }
        )
        self.assertEqual(parsed.checks[0].status, "COMPLETED")
        self.assertEqual(parsed.checks[0].conclusion, "SUCCESS")


class GateVerdictTests(unittest.TestCase):
    def green(self):
        return [
            run(name="test", workflow="Aquarium regression QA"),
            run(name="motor", workflow="Motor Gate"),
            run(name="mesh", workflow="Mesh contract"),
        ]

    def test_ready_requires_exact_head(self):
        result = evaluate_checks(self.green())
        self.assertEqual(result.verdict, "ready")
        self.assertEqual(result.code, "READY")
        self.assertEqual(result.exit_code, EXIT_READY)
        self.assertEqual(result.matched, (
            "Aquarium regression QA / test", "Motor Gate / motor", "Mesh contract / mesh"
        ))

    def test_expected_head_is_mandatory(self):
        result = evaluate_checks(self.green(), expected_head_sha=None)
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "EXPECTED_HEAD_REQUIRED")

    def test_head_mismatch_rejects_even_with_green_checks(self):
        result = evaluate_checks(self.green(), expected_head_sha=OTHER)
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "HEAD_MISMATCH")

    def test_base_mismatch_rejects(self):
        result = evaluate_checks(self.green(), expected_base="release")
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "BASE_MISMATCH")

    def test_malformed_expected_head_raises(self):
        for oid in ("zzz", HEAD[:7], HEAD[:39], HEAD + "f", HEAD + "\n", " " + HEAD):
            with self.subTest(oid=oid), self.assertRaises(ValueError):
                evaluate_checks(self.green(), expected_head_sha=oid)

    def test_pending_check_defers(self):
        checks = self.green() + [run(status="IN_PROGRESS", conclusion=None, name="mesh", workflow="Mesh contract")]
        result = evaluate_checks(checks)
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "CHECK_PENDING")
        self.assertEqual(result.exit_code, EXIT_DEFER)

    def test_queued_check_defers(self):
        checks = [run(status="QUEUED", conclusion=None, name="test", workflow="Aquarium regression QA")]
        checks += self.green()
        result = evaluate_checks(checks)
        self.assertEqual(result.verdict, "defer")

    def test_missing_required_check_defers(self):
        result = evaluate_checks([run(name="test", workflow="Aquarium regression QA")])
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "MISSING_REQUIRED_CHECK")

    def test_failed_check_rejects(self):
        for conclusion in [
            "FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STALE", "ERROR",
        ]:
            with self.subTest(conclusion=conclusion):
                checks = [run(conclusion=conclusion, name="mesh", workflow="Mesh contract")]
                result = evaluate_checks(checks)
                self.assertEqual(result.verdict, "reject")
                self.assertEqual(result.code, "CHECK_FAILED")

    def test_failure_wins_over_pending(self):
        checks = [run(conclusion="FAILURE", name="mesh", workflow="Mesh contract")]
        checks += [run(status="QUEUED", conclusion=None, name="test", workflow="Aquarium regression QA")]
        result = evaluate_checks(checks)
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "CHECK_FAILED")

    def test_negative_conclusion_rejects_even_with_inconsistent_status(self):
        for status in ("IN_PROGRESS", "QUEUED", "WAITING", "bogus"):
            with self.subTest(status=status):
                checks = self.green() + [
                    run(name="mesh", workflow="Mesh contract", status=status, conclusion="FAILURE"),
                ]
                result = evaluate_checks(checks)
                self.assertEqual(result.verdict, "reject")
                self.assertEqual(result.code, "CHECK_FAILED")

    def test_skipped_check_requires_a_waiver(self):
        checks = self.green()[:2] + [
            run(name="mesh", conclusion="SKIPPED", workflow="Mesh contract")
        ]
        result = evaluate_checks(checks)
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "CHECK_SKIPPED_UNWAIVED")

        waived = evaluate_checks(checks, waivers={"Mesh contract / mesh": "docs-only change"})
        self.assertEqual(waived.verdict, "ready")

    def test_waiver_never_masks_a_failure(self):
        checks = [
            run(name="mesh", conclusion="FAILURE", workflow="Mesh contract")
        ]
        result = evaluate_checks(checks, waivers={"Mesh contract / mesh": "flaky"})
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "CHECK_FAILED")

    def test_neutral_conclusion_passes(self):
        checks = self.green()[:2] + [
            run(name="mesh", conclusion="NEUTRAL", workflow="Mesh contract")
        ]
        result = evaluate_checks(checks)
        self.assertEqual(result.verdict, "ready")

    def test_unknown_conclusion_rejects(self):
        checks = [run(conclusion="SOMETHING_NEW", name="mesh", workflow="Mesh contract")]
        result = evaluate_checks(checks)
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "CHECK_UNKNOWN_CONCLUSION")

    def test_unknown_status_defers(self):
        checks = [run(status="MYSTERY", conclusion=None, name="mesh", workflow="Mesh contract")]
        result = evaluate_checks(checks, required=("Mesh contract / mesh",))
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "CHECK_UNKNOWN_STATUS")

    def test_stale_check_head_sha_defers(self):
        checks = [run(head_sha=OTHER, name="mesh", workflow="Mesh contract")]
        result = evaluate_checks(checks, required=("Mesh contract / mesh",))
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "STALE_CHECK")

    def test_all_explicit_matrix_jobs_must_pass(self):
        checks = [
            run(name="linux", workflow="Aquarium regression QA"),
            run(name="windows", conclusion="FAILURE", workflow="Aquarium regression QA"),
            run(name="motor", workflow="Motor Gate"),
            run(name="mesh", workflow="Mesh contract"),
        ]
        required = (
            "Aquarium regression QA / linux",
            "Aquarium regression QA / windows",
            "Motor Gate / motor", "Mesh contract / mesh",
        )
        result = evaluate_checks(checks, required=required)
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "CHECK_FAILED")

    def test_missing_matrix_siblings_defer_even_with_green_first_job(self):
        # Status rollup is not a declared inventory: 3.12/3.13 may not yet
        # exist when 3.11 completes. Enumerate expected jobs explicitly.
        checks = [run(name="aquarium-stdlib (3.11)",
                      workflow="Mojum R0.3 online-learning aquarium regression")]
        result = evaluate_checks(checks, required=(
            "aquarium-stdlib (3.11)", "aquarium-stdlib (3.12)",
            "aquarium-stdlib (3.13)",
        ))
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "MISSING_REQUIRED_CHECK")

    def test_wildcard_cannot_certify_unregistered_matrix_jobs(self):
        checks = [run(name="aquarium-stdlib (3.11)",
                      workflow="Mojum R0.3 online-learning aquarium regression")]
        result = evaluate_checks(checks, required=("aquarium-stdlib (*)",))
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "AMBIGUOUS_REQUIRED_PATTERN")

    def test_workflow_only_selector_cannot_certify_partial_matrix(self):
        checks = [run(name="aquarium-stdlib (3.11)",
                      workflow="Mojum R0.3 online-learning aquarium regression")]
        result = evaluate_checks(checks, required=(
            "Mojum R0.3 online-learning aquarium regression",
        ))
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "MISSING_REQUIRED_CHECK")

    def test_draft_pr_defers(self):
        result = evaluate_checks(self.green(), is_draft=True)
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "DRAFT")

    def test_conflicting_pr_rejects(self):
        result = evaluate_checks(self.green(), mergeable="CONFLICTING")
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "CONFLICTING")

    def test_unknown_mergeability_defers(self):
        result = evaluate_checks(self.green(), mergeable="UNKNOWN")
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "MERGEABLE_UNKNOWN")

    def test_non_clean_merge_state_defers(self):
        for state in ["BLOCKED", "BEHIND", "UNSTABLE", "DIRTY", "DRAFT"]:
            with self.subTest(state=state):
                result = evaluate_checks(self.green(), merge_state_status=state)
                self.assertEqual(result.verdict, "defer")
                self.assertEqual(result.code, "MERGE_STATE_NOT_CLEAN")

    def test_missing_merge_state_defers(self):
        result = evaluate_checks(self.green(), merge_state_status=None)
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "MERGE_STATE_UNKNOWN")

    def test_no_required_patterns_rejects(self):
        result = evaluate_checks(self.green(), required=())
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "NO_REQUIRED_CHECKS")

    def test_exact_full_label_requirement(self):
        checks = [run(name="linux", workflow="Aquarium regression QA")]
        result = evaluate_checks(
            checks,
            required=("Aquarium regression QA / linux",),
        )
        self.assertEqual(result.verdict, "ready")

    def test_exact_full_label_requires_the_named_workflow(self):
        result = evaluate_checks(
            [run(name="Aquarium regression QA / linux", workflow="Other QA")],
            required=("Aquarium regression QA / linux",),
        )
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "MISSING_REQUIRED_CHECK")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def snapshot(self, checks=None):
        return {
            "number": 999,
            "headRefOid": HEAD,
            "baseRefName": "main",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": checks
            if checks is not None
            else [
                run(name="test", workflow="Aquarium regression QA"),
                run(name="motor", workflow="Motor Gate"),
                run(name="mesh", workflow="Mesh contract"),
            ],
        }

    def write(self, payload, name="pr.json"):
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, json.loads(out.getvalue()), err.getvalue()

    def base_args(self, path):
        return [
            "--input", str(path),
            "--expected-head-sha", HEAD,
            "--require", "Aquarium regression QA / test",
            "--require", "Motor Gate / motor",
            "--require", "Mesh contract / mesh",
        ]

    def test_ready_exit_zero_and_never_merges(self):
        code, payload, err = self.invoke(*self.base_args(self.write(self.snapshot())))
        self.assertEqual(code, EXIT_READY)
        self.assertEqual(err, "")
        self.assertEqual(payload["schema"], SCHEMA)
        self.assertEqual(payload["verdict"], "ready")
        self.assertFalse(payload["merge_performed"])

    def test_defer_exit_two(self):
        payload = self.snapshot([
            run(status="QUEUED", conclusion=None, name="mesh", workflow="Mesh contract"),
        ])
        code, result, _ = self.invoke(*self.base_args(self.write(payload)))
        self.assertEqual(code, EXIT_DEFER)
        self.assertEqual(result["verdict"], "defer")
        self.assertEqual(result["exit_code"], EXIT_DEFER)

    def test_reject_exit_one(self):
        payload = self.snapshot([run(conclusion="FAILURE", name="mesh", workflow="Mesh contract")])
        code, result, _ = self.invoke(
            *self.base_args(self.write(payload))
        )
        self.assertEqual(code, EXIT_REJECT)
        self.assertEqual(result["verdict"], "reject")

    def test_waiver_option_restores_ready(self):
        checks = [
            run(name="test", workflow="Aquarium regression QA"),
            run(name="motor", workflow="Motor Gate"),
            run(name="mesh", conclusion="SKIPPED", workflow="Mesh contract"),
        ]
        args = self.base_args(self.write(self.snapshot(checks))) + [
            "--waive",
            "Mesh contract / mesh=docs-only",
        ]
        code, result, _ = self.invoke(*args)
        self.assertEqual(code, EXIT_READY)
        self.assertEqual(result["waivers"], {"Mesh contract / mesh": "docs-only"})

    def test_stdin_input(self):
        path = self.write(self.snapshot())
        argv = self.base_args("-")
        stdin = io.StringIO(path.read_text(encoding="utf-8"))
        with mock.patch("sys.stdin", stdin):
            code, result, _ = self.invoke(*argv)
        self.assertEqual(code, EXIT_READY)

    def test_invalid_snapshot_reports_invalid_input(self):
        path = self.write({"headRefOid": "nope"})
        code, result, _ = self.invoke(*self.base_args(path))
        self.assertEqual(code, EXIT_REJECT)
        self.assertEqual(result["code"], "INVALID_INPUT")
        self.assertFalse(result["merge_performed"])

    def test_bad_waiver_syntax_is_invalid_input(self):
        path = self.write(self.snapshot())
        code, result, _ = self.invoke(*(self.base_args(path) + ["--waive", "no-reason"]))
        self.assertEqual(code, EXIT_REJECT)
        self.assertEqual(result["code"], "INVALID_INPUT")

    def test_usage_error_has_distinct_exit_code(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["--input", "missing.json"])
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("usage:", err.getvalue())

    def test_missing_file_is_invalid_input(self):
        code, result, _ = self.invoke(*self.base_args(self.root / "absent.json"))
        self.assertEqual(code, EXIT_REJECT)
        self.assertEqual(result["code"], "INVALID_INPUT")

    def test_pr_mode_uses_gh_and_reports_snapshot(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(self.snapshot()), stderr=""
        )
        with mock.patch("mojum.merge_gate.subprocess.run", return_value=completed) as runner:
            code, result, _ = self.invoke(
                "--pr", "419",
                "--expected-head-sha", HEAD,
                "--require", "Aquarium regression QA / test",
                "--require", "Motor Gate / motor",
                "--require", "Mesh contract / mesh",
            )
        self.assertEqual(code, EXIT_READY)
        self.assertEqual(result["number"], 999)
        command = runner.call_args.args[0]
        self.assertEqual(command[:3], ["gh", "pr", "view"])
        self.assertIn("statusCheckRollup", command[-1])

    def test_pr_mode_failure_is_invalid_input(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not found"
        )
        with mock.patch("mojum.merge_gate.subprocess.run", return_value=completed):
            code, result, _ = self.invoke(
                "--pr", "1", "--expected-head-sha", HEAD, "--require", "x"
            )
        self.assertEqual(code, EXIT_REJECT)
        self.assertEqual(result["code"], "INVALID_INPUT")
        self.assertIn("not found", result["error"])

    def test_fetch_snapshot_rejects_bad_pr_number(self):
        for value in [0, -1, True, "419"]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    fetch_snapshot(value)  # type: ignore[arg-type]

    def test_fetch_snapshot_wraps_os_error(self):
        with mock.patch("mojum.merge_gate.subprocess.run", side_effect=OSError("boom")):
            with self.assertRaises(FetchError):
                fetch_snapshot(1)


class CheckOutcomeTests(unittest.TestCase):
    def test_label_includes_workflow(self):
        check = CheckOutcome(name="test", status="COMPLETED", workflow="Aquarium")
        self.assertEqual(check.label, "Aquarium / test")


if __name__ == "__main__":
    unittest.main()
