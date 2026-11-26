"""A supplied legacy state must not disappear behind completed run evidence."""

from copy import deepcopy
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from mojum.merge_gate import (
    MAIN_REQUIRED_JOBS, SnapshotError, evaluate, parse_snapshot,
)


HEAD = "a" * 40
BASE = "b" * 40
ROOT = Path(__file__).resolve().parents[1]
ALIASES = ("statusCheckRollup", "checks")
PENDING = ("PENDING", "EXPECTED")
UNKNOWN = ("MYSTERY", "COMPLETED", "SUCCESSFUL", "NEUTRAL")
FAILURES = ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STALE", "STARTUP_FAILURE")


def snapshot(*, typed=True, alias="statusCheckRollup", dual=False):
    checks = []
    for label in MAIN_REQUIRED_JOBS:
        workflow, name = label.split(" / ", 1)
        check = {
            "name": name, "workflowName": workflow, "headSha": HEAD,
            "status": "COMPLETED", "conclusion": "SUCCESS",
        }
        if typed:
            check["__typename"] = "CheckRun"
        checks.append(check)
    document = {
        "headRefOid": HEAD, "baseRefName": "main", "baseRefOid": BASE,
        "currentBaseRefOid": BASE, "isDraft": False,
        "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", alias: checks,
    }
    if dual:
        document[ALIASES[1 - ALIASES.index(alias)]] = deepcopy(checks)
    return document


def decision(document, *, required=()):
    return evaluate(parse_snapshot(document), expected_head_sha=HEAD,
                    expected_base_sha=BASE, required=required)


class MergeGateHybridStateTests(unittest.TestCase):
    def test_pending_state_defers_completed_run_in_either_shape_and_rollup(self):
        for state, typed, alias in itertools.product(PENDING, (True, False), ALIASES):
            with self.subTest(state=state, typed=typed, alias=alias):
                document = snapshot(typed=typed, alias=alias)
                document[alias][0]["state"] = state
                result = decision(document)
                self.assertEqual((result.verdict, result.code), ("defer", "CHECK_PENDING"))

    def test_pending_state_conflicts_with_success_in_both_rollup_directions(self):
        for state, typed, alias in itertools.product(PENDING, (True, False), ALIASES):
            with self.subTest(state=state, typed=typed, alias=alias):
                document = snapshot(typed=typed, dual=True)
                document[alias][0]["state"] = state
                with self.assertRaisesRegex(SnapshotError, "conflicting aliases"):
                    parse_snapshot(document)

    def test_consistent_pending_rollups_still_defer_after_normalization(self):
        for state, typed in itertools.product(PENDING, (True, False)):
            with self.subTest(state=state, typed=typed):
                document = snapshot(typed=typed, dual=True)
                document["statusCheckRollup"][0]["state"] = " " + state.lower() + " "
                document["checks"][0]["state"] = state
                result = decision(document)
                self.assertEqual((result.verdict, result.code), ("defer", "CHECK_PENDING"))

    def test_unknown_state_cannot_certify_completed_success(self):
        for state, typed, alias in itertools.product(UNKNOWN, (True, False), ALIASES):
            with self.subTest(state=state, typed=typed, alias=alias):
                document = snapshot(typed=typed, alias=alias)
                document[alias][0]["state"] = state
                result = decision(document)
                self.assertEqual((result.verdict, result.code), ("defer", "CHECK_UNKNOWN_STATUS"))

    def test_unknown_state_conflicts_with_success_in_both_rollup_directions(self):
        for state, typed, alias in itertools.product(UNKNOWN, (True, False), ALIASES):
            with self.subTest(state=state, typed=typed, alias=alias):
                document = snapshot(typed=typed, dual=True)
                document[alias][0]["state"] = state
                with self.assertRaisesRegex(SnapshotError, "conflicting aliases"):
                    parse_snapshot(document)

    def test_supplied_malformed_state_is_not_treated_as_absent(self):
        malformed = (None, "", " \t", False, 0, 1, [], {}, ["PENDING"], {"value": "PENDING"})
        for state, typed, alias, dual in itertools.product(
                malformed, (True, False), ALIASES, (False, True)):
            with self.subTest(state=state, typed=typed, alias=alias, dual=dual):
                document = snapshot(typed=typed, alias=alias, dual=dual)
                document[alias][0]["state"] = state
                with self.assertRaisesRegex(SnapshotError, "state"):
                    parse_snapshot(document)

    def test_pending_or_unknown_state_preserves_failed_conclusion_priority(self):
        for state, typed, conclusion in itertools.product(
                (*PENDING, *UNKNOWN), (True, False), FAILURES):
            with self.subTest(state=state, typed=typed, conclusion=conclusion):
                document = snapshot(typed=typed)
                document["statusCheckRollup"][0].update(state=state, conclusion=conclusion)
                result = decision(document)
                self.assertEqual((result.verdict, result.code), ("reject", "CHECK_FAILED"))

    def test_failure_and_error_state_still_override_success_conclusion(self):
        for state, typed, status in itertools.product(
                ("FAILURE", "ERROR"), (True, False), ("COMPLETED", "IN_PROGRESS", "MYSTERY", None)):
            with self.subTest(state=state, typed=typed, status=status):
                document = snapshot(typed=typed)
                document["statusCheckRollup"][0].update(state=state, status=status)
                result = decision(document)
                self.assertEqual((result.verdict, result.code), ("reject", "CHECK_FAILED"))

    def test_success_state_does_not_override_unfinished_or_unknown_run_status(self):
        for typed, status in itertools.product((True, False), ("QUEUED", "IN_PROGRESS", "MYSTERY")):
            with self.subTest(typed=typed, status=status):
                document = snapshot(typed=typed)
                document["statusCheckRollup"][0].update(state="SUCCESS", status=status)
                result = decision(document)
                expected = "CHECK_UNKNOWN_STATUS" if status == "MYSTERY" else "CHECK_PENDING"
                self.assertEqual((result.verdict, result.code), ("defer", expected))

    def test_successful_check_run_forms_remain_ready(self):
        for typed, with_state in itertools.product((True, False), (False, True)):
            with self.subTest(typed=typed, with_state=with_state):
                document = snapshot(typed=typed)
                if with_state:
                    document["statusCheckRollup"][0]["state"] = " success "
                self.assertEqual(decision(document).verdict, "ready")

    def test_valid_legacy_status_context_forms_remain_supported(self):
        outcomes = {
            "SUCCESS": ("ready", "READY"), "FAILURE": ("reject", "CHECK_FAILED"),
            "ERROR": ("reject", "CHECK_FAILED"), "PENDING": ("defer", "CHECK_PENDING"),
            "EXPECTED": ("defer", "CHECK_PENDING"),
        }
        for typed, state in itertools.product((True, False), outcomes):
            with self.subTest(typed=typed, state=state):
                document = snapshot()
                context = {"context": "external/security", "state": state}
                if typed:
                    context["__typename"] = "StatusContext"
                document["statusCheckRollup"].append(context)
                result = decision(document, required=("external/security",))
                self.assertEqual((result.verdict, result.code), outcomes[state])

    def cli(self, document):
        result = subprocess.run(
            [sys.executable, "-m", "mojum.merge_gate", "--input", "-",
             "--expected-head-sha", HEAD, "--expected-base-sha", BASE],
            input=json.dumps(document), capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=str(ROOT / "src")), cwd=ROOT,
            timeout=10, check=False,
        )
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertFalse(report["merge_performed"])
        self.assertEqual(result.returncode, report["exit_code"])
        return result.returncode, report["verdict"], report["code"]

    def test_cli_pending_state_and_conflicting_aliases_never_report_ready(self):
        for state, typed, alias, dual in itertools.product(PENDING, (True, False), ALIASES, (False, True)):
            with self.subTest(state=state, typed=typed, alias=alias, dual=dual):
                document = snapshot(typed=typed, alias=alias, dual=dual)
                document[alias][0]["state"] = state
                expected = (1, "reject", "INVALID_INPUT") if dual else (2, "defer", "CHECK_PENDING")
                self.assertEqual(self.cli(document), expected)

    def test_cli_unknown_and_malformed_state_never_report_ready(self):
        for state, typed in itertools.product(("COMPLETED", None, "", {}), (True, False)):
            with self.subTest(state=state, typed=typed):
                document = snapshot(typed=typed)
                document["statusCheckRollup"][0]["state"] = state
                expected = (2, "defer", "CHECK_UNKNOWN_STATUS") if state == "COMPLETED" else (1, "reject", "INVALID_INPUT")
                self.assertEqual(self.cli(document), expected)


if __name__ == "__main__":
    unittest.main()
