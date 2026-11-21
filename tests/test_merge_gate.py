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
BASE = "c" * 40
OLD_BASE = "d" * 40
REQUIRED = ("Aquarium regression QA / test", "Motor Gate / motor", "Mesh contract / mesh")
MAIN_MATRIX_CHECKS = (
    ("Mojum R0.3 online-learning aquarium regression", "aquarium-stdlib"),
    ("Mojum motor-gate independent QA", "motor-gate-stdlib"),
    ("Mojum mesh contract independent QA", "mesh-stdlib"),
)


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
        base_ref="release",
        base_sha=BASE,
        current_base_sha=BASE,
        checks=tuple(checks),
        number=419,
        is_draft=False,
        mergeable="MERGEABLE",
        merge_state_status="CLEAN",
    )
    defaults.update(overrides)
    return PullRequestFacts(**defaults)


def evaluate_checks(checks, **overrides):
    fact_fields = {
        "head_sha", "base_ref", "base_sha", "current_base_sha", "is_draft",
        "mergeable", "merge_state_status",
    }
    fact_overrides = {k: v for k, v in overrides.items() if k in fact_fields}
    kwargs = {k: v for k, v in overrides.items() if k not in fact_fields}
    kwargs.setdefault("expected_head_sha", HEAD)
    kwargs.setdefault("expected_base_sha", BASE)
    kwargs.setdefault("expected_base", fact_overrides.get("base_ref", "release"))
    kwargs.setdefault("required", REQUIRED)
    parsed = parse_snapshot(
        {
            "headRefOid": HEAD,
            "baseRefOid": BASE,
            "baseRefName": "main",
            "currentBaseRefOid": BASE,
            "checks": list(checks),
        }
    )
    return evaluate(facts(parsed.checks, **fact_overrides), **kwargs)


class SnapshotParsingTests(unittest.TestCase):
    def test_gh_camel_case_rollup_with_check_run_and_status_context(self):
        snapshot = {
            "number": 415,
            "headRefOid": HEAD,
            "baseRefOid": BASE,
            "baseRefName": "main",
            "currentBaseRefOid": BASE,
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
        self.assertEqual(parsed.base_sha, BASE)
        self.assertEqual(parsed.current_base_sha, BASE)
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
            "base_sha": BASE,
            "current_base_sha": BASE,
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

    def test_snapshot_rejects_malformed_or_conflicting_base_object_ids(self):
        for oid in (BASE[:7], BASE[:39], BASE + "f", BASE + "\n", " " + BASE):
            with self.subTest(oid=oid), self.assertRaises(SnapshotError):
                parse_snapshot({
                    "headRefOid": HEAD,
                    "baseRefName": "main",
                    "baseRefOid": oid,
                })
        with self.assertRaisesRegex(SnapshotError, "conflicting aliases"):
            parse_snapshot({
                "headRefOid": HEAD,
                "baseRefName": "main",
                "baseRefOid": BASE,
                "base_sha": OLD_BASE,
            })
        with self.assertRaisesRegex(SnapshotError, "conflicting aliases"):
            parse_snapshot({
                "headRefOid": HEAD,
                "baseRefName": "main",
                "base_ref": "release",
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
        result = evaluate_checks(self.green(), expected_base="main")
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "BASE_MISMATCH")

    def test_expected_base_sha_is_mandatory(self):
        result = evaluate_checks(self.green(), expected_base_sha=None)
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "EXPECTED_BASE_SHA_REQUIRED")

    def test_missing_pr_base_sha_rejects(self):
        result = evaluate_checks(self.green(), base_sha=None)
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "BASE_SHA_REQUIRED")

    def test_stale_base_defers_even_when_merge_state_is_clean(self):
        result = evaluate_checks(self.green(), base_sha=OLD_BASE)
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "STALE_BASE")

    def test_caller_base_sha_must_match_independent_live_base(self):
        result = evaluate_checks(self.green(), expected_base_sha=OLD_BASE)
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "CURRENT_BASE_SHA_MISMATCH")

    def test_blank_expected_base_is_invalid(self):
        with self.assertRaisesRegex(ValueError, "expected_base"):
            evaluate_checks(self.green(), expected_base="")

    def test_malformed_expected_head_raises(self):
        for oid in ("zzz", HEAD[:7], HEAD[:39], HEAD + "f", HEAD + "\n", " " + HEAD):
            with self.subTest(oid=oid), self.assertRaises(ValueError):
                evaluate_checks(self.green(), expected_head_sha=oid)

    def test_malformed_expected_base_sha_raises(self):
        for oid in ("zzz", BASE[:7], BASE[:39], BASE + "f", BASE + "\n", " " + BASE):
            with self.subTest(oid=oid), self.assertRaises(ValueError):
                evaluate_checks(self.green(), expected_base_sha=oid)

    def test_expected_head_with_trailing_newline_raises(self):
        for value in ["a" * 40 + "\n", "abcdef1\n", "abcdef1 ", "\nabcdef1"]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    evaluate(
                        facts(self.green()),
                        expected_head_sha=value,
                        required=REQUIRED,
                    )

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
        # A partially refreshed CI rollup can contradict itself: an explicit
        # failure must not become a merely deferred pending check.
        for status in ("IN_PROGRESS", "QUEUED", "WAITING", "bogus"):
            with self.subTest(status=status):
                checks = self.green() + [
                    run(name="mesh", workflow="Mesh contract", status=status, conclusion="FAILURE"),
                ]
                result = evaluate_checks(checks)
                self.assertEqual(result.verdict, "reject")
                self.assertEqual(result.code, "CHECK_FAILED")

    def test_negative_legacy_state_or_conclusion_rejects(self):
        for fields in (
            {"state": "SUCCESS", "conclusion": "FAILURE"},
            {"state": "ERROR", "status": "COMPLETED", "conclusion": "SUCCESS"},
        ):
            with self.subTest(fields=fields):
                checks = [{"context": "legacy/gate", **fields}]
                result = evaluate_checks(checks, required=("legacy/gate",))
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
        workflow = "Mojum R0.3 online-learning aquarium regression"
        result = evaluate_checks(checks, required=(
            f"{workflow} / aquarium-stdlib (3.11)",
            f"{workflow} / aquarium-stdlib (3.12)",
            f"{workflow} / aquarium-stdlib (3.13)",
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
        required = ("Aquarium regression QA / linux",)
        substitutions = (
            run(name="linux", workflow="Other QA"),
            run(name=required[0], workflow="Other QA"),
            {"context": required[0], "state": "SUCCESS"},
        )
        for check in substitutions:
            with self.subTest(check=check):
                result = evaluate_checks([check], required=required)
                self.assertEqual(result.verdict, "defer")
                self.assertEqual(result.code, "MISSING_REQUIRED_CHECK")

    def test_actions_job_requires_workflow_qualified_selector(self):
        # Job names are not globally unique. A similarly named job from a
        # different workflow must not satisfy the intended requirement.
        checks = [run(name="actual-chrome", workflow="Unrelated workflow")]
        result = evaluate_checks(checks, required=("actual-chrome",))
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "AMBIGUOUS_REQUIRED_PATTERN")

    def test_bare_legacy_commit_status_remains_supported(self):
        checks = [{"context": "external/security", "state": "SUCCESS"}]
        result = evaluate_checks(checks, required=("external/security",))
        self.assertEqual(result.verdict, "ready")

    def test_duplicate_extra_workflow_job_defers(self):
        check = run(name="actual-chrome", workflow="Viewer smoke")
        result = evaluate_checks(
            [check, check], required=("Viewer smoke / actual-chrome",),
        )
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "AMBIGUOUS_REQUIRED_CHECK")

    def test_extra_workflow_job_requires_explicit_run_status(self):
        check = run(name="actual-chrome", workflow="Viewer smoke")
        check["status"] = None
        result = evaluate_checks(
            [check], required=("Viewer smoke / actual-chrome",),
        )
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "REQUIRED_RUN_STATUS_MISSING")


class MainMatrixTests(unittest.TestCase):
    def green(self):
        return [
            run(name=f"{job} ({version})", workflow=workflow)
            for workflow, job in MAIN_MATRIX_CHECKS
            for version in ("3.11", "3.12", "3.13")
        ]

    def test_complete_main_matrices_ready_without_extra_requirements(self):
        result = evaluate_checks(self.green(), base_ref="main", required=())
        self.assertEqual(result.verdict, "ready")
        self.assertEqual(len(result.required), 9)

    def test_bare_narrow_selector_is_rejected_and_cannot_bypass_main_matrices(self):
        check = self.green()[0]
        result = evaluate_checks([check], base_ref="main", required=(check["name"],))
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.code, "AMBIGUOUS_REQUIRED_PATTERN")

    def test_same_job_name_in_another_workflow_cannot_fill_main_matrix(self):
        checks = self.green()
        checks[0]["workflowName"] = "Unrelated CI"
        result = evaluate_checks(checks, base_ref="main", required=())
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "MISSING_REQUIRED_CHECK")

    def test_main_job_requires_explicit_workflow_run_completion(self):
        for override in ({"status": None}, {"__typename": "StatusContext"}):
            with self.subTest(override=override):
                checks = self.green()
                checks[0].update(override)
                result = evaluate_checks(checks, base_ref="main", required=())
                self.assertEqual(result.verdict, "defer")
                self.assertEqual(result.code, "REQUIRED_RUN_STATUS_MISSING")

    def test_duplicate_main_job_defers(self):
        checks = self.green()
        result = evaluate_checks([*checks, checks[0]], base_ref="main", required=())
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "AMBIGUOUS_REQUIRED_CHECK")

    def test_null_check_head_alias_does_not_hide_stale_head(self):
        checks = self.green()
        checks[0].update(headSha=None, head_sha=OTHER)
        result = evaluate_checks(checks, base_ref="main", required=())
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "STALE_CHECK")

        checks[0]["headSha"] = HEAD
        with self.assertRaisesRegex(SnapshotError, "conflicting aliases"):
            evaluate_checks(checks, base_ref="main", required=())

    def test_main_jobs_require_success_even_with_waiver(self):
        for conclusion in ("SKIPPED", "NEUTRAL"):
            with self.subTest(conclusion=conclusion):
                checks = self.green()
                check = checks[0]
                check["conclusion"] = conclusion
                label = f"{check['workflowName']} / {check['name']}"
                result = evaluate_checks(
                    checks, base_ref="main", required=(), waivers={label: "docs-only"},
                )
                self.assertEqual(result.verdict, "reject")
                self.assertEqual(result.code, "CHECK_NOT_SUCCESS")

    def test_extra_main_jobs_also_require_success(self):
        for conclusion in ("SKIPPED", "NEUTRAL"):
            with self.subTest(conclusion=conclusion):
                checks = self.green()
                extra = run(
                    name="actual-chrome", workflow="Viewer smoke",
                    conclusion=conclusion,
                )
                result = evaluate_checks(
                    [*checks, extra],
                    base_ref="main",
                    required=("Viewer smoke / actual-chrome",),
                    waivers={"Viewer smoke / actual-chrome": "not a main override"},
                )
                self.assertEqual(result.verdict, "reject")
                self.assertEqual(result.code, "CHECK_NOT_SUCCESS")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def snapshot(self, checks=None):
        return {
            "number": 419,
            "headRefOid": HEAD,
            "baseRefOid": BASE,
            "baseRefName": "release",
            "currentBaseRefOid": BASE,
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
            "--expected-base-sha", BASE,
            "--expected-base", "release",
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
        self.assertEqual(payload["base_sha"], BASE)
        self.assertEqual(payload["expected_base_sha"], BASE)
        self.assertFalse(payload["merge_performed"])

    def test_offline_snapshot_without_base_evidence_rejects(self):
        snapshot = self.snapshot()
        snapshot.pop("baseRefOid")
        snapshot.pop("currentBaseRefOid")
        args = [arg for arg in self.base_args(self.write(snapshot))]
        index = args.index("--expected-base-sha")
        del args[index:index + 2]
        code, payload, _ = self.invoke(*args)
        self.assertEqual(code, EXIT_REJECT)
        self.assertEqual(payload["code"], "EXPECTED_BASE_SHA_REQUIRED")

    def test_main_floor_runs_without_require_flags(self):
        snapshot = self.snapshot([
            run(name="aquarium-stdlib (3.11)", workflow=MAIN_MATRIX_CHECKS[0][0]),
        ])
        snapshot["baseRefName"] = "main"
        code, payload, _ = self.invoke(
            "--input", str(self.write(snapshot)), "--expected-head-sha", HEAD,
            "--expected-base-sha", BASE,
        )
        self.assertEqual(code, EXIT_DEFER)
        self.assertEqual(payload["code"], "MISSING_REQUIRED_CHECK")
        self.assertEqual(len(payload["required"]), 9)

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
        view = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(self.snapshot()), stderr=""
        )
        pull = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({
                "number": 419,
                "head": {"sha": HEAD},
                "base": {
                    "ref": "release",
                    "sha": BASE,
                    "repo": {"full_name": "Garyu-tt/mojum"},
                },
            }), stderr="",
        )
        current = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({"object": {"sha": BASE}}), stderr=""
        )
        with mock.patch(
            "mojum.merge_gate.subprocess.run", side_effect=[view, pull, current]
        ) as runner:
            code, result, _ = self.invoke(
                "--pr", "419",
                "--expected-head-sha", HEAD,
                "--expected-base-sha", BASE,
                "--expected-base", "release",
                "--require", "Aquarium regression QA / test",
                "--require", "Motor Gate / motor",
                "--require", "Mesh contract / mesh",
            )
        self.assertEqual(code, EXIT_READY)
        self.assertEqual(result["number"], 419)
        self.assertEqual(result["base_sha"], BASE)
        self.assertEqual(result["current_base_sha"], BASE)
        commands = [call.args[0] for call in runner.call_args_list]
        self.assertEqual(commands[0][:3], ["gh", "pr", "view"])
        self.assertIn("statusCheckRollup", commands[0][-1])
        self.assertEqual(commands[1][:2], ["gh", "api"])
        self.assertIn("/pulls/419", commands[1][2])
        self.assertIn("/git/ref/heads/release", commands[2][2])

    def test_pr_mode_rejects_a_head_that_changes_between_queries(self):
        view = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(self.snapshot()), stderr=""
        )
        pull = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({
                "number": 419,
                "head": {"sha": OTHER},
                "base": {
                    "ref": "release",
                    "sha": BASE,
                    "repo": {"full_name": "Garyu-tt/mojum"},
                },
            }), stderr="",
        )
        with mock.patch(
            "mojum.merge_gate.subprocess.run", side_effect=[view, pull]
        ):
            code, result, _ = self.invoke(
                "--pr", "419", "--expected-head-sha", HEAD,
                "--expected-base", "release", "--require", "x",
            )
        self.assertEqual(code, EXIT_REJECT)
        self.assertEqual(result["code"], "INVALID_INPUT")
        self.assertIn("head changed", result["error"])

    def test_pr_mode_rejects_base_retarget_between_queries(self):
        view = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(self.snapshot()), stderr=""
        )
        pull = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({
                "number": 419,
                "head": {"sha": HEAD},
                "base": {
                    "ref": "main",
                    "sha": BASE,
                    "repo": {"full_name": "Garyu-tt/mojum"},
                },
            }), stderr="",
        )
        with mock.patch(
            "mojum.merge_gate.subprocess.run", side_effect=[view, pull]
        ):
            code, result, _ = self.invoke(
                "--pr", "419", "--expected-head-sha", HEAD,
                "--expected-base-sha", BASE, "--expected-base", "release",
                "--require", "x",
            )
        self.assertEqual(code, EXIT_REJECT)
        self.assertEqual(result["code"], "INVALID_INPUT")
        self.assertIn("base changed", result["error"])

    def test_pr_mode_failure_is_invalid_input(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not found"
        )
        with mock.patch("mojum.merge_gate.subprocess.run", return_value=completed):
            code, result, _ = self.invoke(
                "--pr", "1", "--expected-head-sha", HEAD,
                "--expected-base-sha", BASE, "--require", "x"
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

    def test_pull_request_facts_preserves_original_positional_field_order(self):
        check = CheckOutcome(name="test", status="COMPLETED")
        snapshot = PullRequestFacts(
            HEAD, "main", (check,), 419, "feature/x", False, "MERGEABLE", "CLEAN"
        )
        self.assertEqual(snapshot.checks, (check,))
        self.assertEqual(snapshot.number, 419)
        self.assertIsNone(snapshot.base_sha)
        self.assertIsNone(snapshot.current_base_sha)


if __name__ == "__main__":
    unittest.main()
