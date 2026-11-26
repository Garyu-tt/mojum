"""Conflicting snapshot aliases must not hide draft or failed CI evidence."""

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import itertools
import json
import unittest
from unittest.mock import patch

from mojum.merge_gate import (
    EXIT_REJECT, MAIN_REQUIRED_JOBS, SnapshotError, evaluate, main, parse_snapshot,
)


HEAD = "a" * 40
BASE = "b" * 40
DRAFT_ALIASES = ("isDraft", "is_draft", "draft")


def snapshot():
    checks = []
    for label in MAIN_REQUIRED_JOBS:
        workflow, name = label.split(" / ", 1)
        checks.append({
            "__typename": "CheckRun", "name": name, "workflowName": workflow,
            "status": "COMPLETED", "conclusion": "SUCCESS", "headSha": HEAD,
        })
    return {
        "number": 419, "headRefOid": HEAD, "baseRefName": "main",
        "baseRefOid": BASE, "currentBaseRefOid": BASE,
        "isDraft": False, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
        "statusCheckRollup": checks,
    }


def verdict(document):
    return evaluate(parse_snapshot(document), expected_head_sha=HEAD,
                    expected_base_sha=BASE).verdict


class MergeGateAliasTests(unittest.TestCase):
    def test_single_draft_aliases_keep_ready_and_draft_verdicts(self):
        for alias, value in itertools.product(DRAFT_ALIASES, (False, True)):
            with self.subTest(alias=alias, value=value):
                document = snapshot()
                del document["isDraft"]
                document[alias] = value
                self.assertEqual(verdict(document), "defer" if value else "ready")

    def test_agreed_draft_aliases_are_accepted(self):
        for value in (False, True):
            with self.subTest(value=value):
                document = snapshot()
                document.update(dict.fromkeys(DRAFT_ALIASES, value))
                self.assertEqual(verdict(document), "defer" if value else "ready")

    def test_conflicting_draft_aliases_are_rejected_in_both_directions(self):
        for first, second in itertools.permutations(DRAFT_ALIASES, 2):
            with self.subTest(first=first, second=second):
                document = snapshot()
                del document["isDraft"]
                document[first], document[second] = False, True
                with self.assertRaisesRegex(SnapshotError, "conflicting aliases"):
                    parse_snapshot(document)

    def test_hidden_invalid_draft_aliases_are_rejected(self):
        for alias, value in itertools.product(DRAFT_ALIASES, (None, 0, 1, "false", [], {})):
            with self.subTest(alias=alias, value=value):
                document = snapshot()
                document.update(dict.fromkeys(DRAFT_ALIASES, False))
                document[alias] = value
                with self.assertRaisesRegex(SnapshotError, "boolean"):
                    parse_snapshot(document)

    def test_single_rollup_aliases_are_accepted(self):
        for alias in ("statusCheckRollup", "checks"):
            with self.subTest(alias=alias):
                document = snapshot()
                document[alias] = document.pop("statusCheckRollup")
                self.assertEqual(verdict(document), "ready")

    def test_check_identity_aliases_preserve_single_and_agreed_names(self):
        document = snapshot()
        for check in document["statusCheckRollup"]:
            check["context"] = " " + check["name"] + " "
            check["workflow"] = " " + check["workflowName"] + " "
        self.assertEqual(verdict(document), "ready")
        for check in document["statusCheckRollup"]:
            del check["name"], check["workflowName"]
        self.assertEqual(verdict(document), "ready")

    def test_conflicting_check_identity_aliases_cannot_certify_required_jobs(self):
        for first, second in (("name", "context"), ("workflowName", "workflow")):
            for conflicting in (first, second):
                with self.subTest(alias=conflicting):
                    document = snapshot()
                    check = document["statusCheckRollup"][0]
                    check[second] = check[first]
                    check[conflicting] = "Unrelated required-check identity"
                    with self.assertRaisesRegex(SnapshotError, "conflicting aliases"):
                        parse_snapshot(document)

    def test_hidden_invalid_check_identity_aliases_are_rejected(self):
        for first, second in (("name", "context"), ("workflowName", "workflow")):
            for invalid, value in itertools.product((first, second), (0, False, [], {})):
                with self.subTest(alias=invalid, value=value):
                    document = snapshot()
                    check = document["statusCheckRollup"][0]
                    check[second] = check[first]
                    check[invalid] = value
                    with self.assertRaises(SnapshotError):
                        parse_snapshot(document)

    def test_agreed_rollups_allow_normalized_values_and_different_order(self):
        document = snapshot()
        document["checks"] = [{
            "name": check["name"], "workflow": check["workflowName"],
            "status": "completed", "conclusion": "success", "head_sha": HEAD.upper(),
        } for check in reversed(document["statusCheckRollup"])]
        self.assertEqual(verdict(document), "ready")

    def test_conflicting_rollups_cannot_hide_failed_pending_or_stale_checks(self):
        for alias, changed in itertools.product(
                ("statusCheckRollup", "checks"),
                ({"conclusion": "FAILURE"}, {"status": "QUEUED", "conclusion": None},
                 {"headSha": "c" * 40}, {"workflowName": "Unrelated workflow"})):
            with self.subTest(alias=alias, changed=changed):
                document = snapshot()
                document["checks"] = deepcopy(document["statusCheckRollup"])
                document[alias][0].update(changed)
                with self.assertRaisesRegex(SnapshotError, "conflicting aliases"):
                    parse_snapshot(document)

    def test_rollup_alias_comparison_preserves_duplicate_checks(self):
        document = snapshot()
        document["checks"] = deepcopy(document["statusCheckRollup"])
        document["checks"].append(deepcopy(document["checks"][0]))
        with self.assertRaisesRegex(SnapshotError, "conflicting aliases"):
            parse_snapshot(document)
        document["statusCheckRollup"] = deepcopy(document["checks"])
        self.assertEqual(verdict(document), "defer")

    def test_missing_explicit_run_status_is_not_equal_to_completed(self):
        document = snapshot()
        document["checks"] = deepcopy(document["statusCheckRollup"])
        del document["checks"][0]["status"]
        with self.assertRaisesRegex(SnapshotError, "conflicting aliases"):
            parse_snapshot(document)

    def test_empty_and_malformed_rollup_aliases_cannot_hide_behind_green_checks(self):
        for alias, value in itertools.product(("statusCheckRollup", "checks"), (None, [], "bad", {}, [None])):
            with self.subTest(alias=alias, value=value):
                document = snapshot()
                document["checks"] = deepcopy(document["statusCheckRollup"])
                document[alias] = value
                with self.assertRaises(SnapshotError):
                    parse_snapshot(document)

    def test_matching_empty_rollups_and_absent_aliases_keep_legacy_defaults(self):
        document = snapshot()
        document.update(statusCheckRollup=None, checks=[])
        self.assertEqual(verdict(document), "defer")
        del document["statusCheckRollup"], document["checks"], document["isDraft"]
        facts = parse_snapshot(document)
        self.assertEqual(facts.checks, ())
        self.assertFalse(facts.is_draft)
        self.assertEqual(verdict(document), "defer")

    def test_merge_state_aliases_are_compared_after_enum_normalization(self):
        for alias in ("mergeStateStatus", "merge_state_status"):
            with self.subTest(alias=alias):
                document = snapshot()
                del document["mergeStateStatus"]
                document[alias] = " clean "
                self.assertEqual(verdict(document), "ready")
        document = snapshot()
        document["merge_state_status"] = " clean "
        self.assertEqual(verdict(document), "ready")

    def test_conflicting_and_invalid_merge_state_aliases_are_rejected(self):
        for alias, value in itertools.product(
                ("mergeStateStatus", "merge_state_status"),
                ("BLOCKED", "DIRTY", None, "", 0, False, [])):
            with self.subTest(alias=alias, value=value):
                document = snapshot()
                document["merge_state_status"] = "CLEAN"
                document[alias] = value
                with self.assertRaises(SnapshotError):
                    parse_snapshot(document)

    def test_unknown_merge_state_preserves_single_absent_and_agreed_null_aliases(self):
        for aliases in ((), ("mergeStateStatus",), ("merge_state_status",),
                        ("mergeStateStatus", "merge_state_status")):
            with self.subTest(aliases=aliases):
                document = snapshot()
                del document["mergeStateStatus"]
                document.update(dict.fromkeys(aliases, None))
                self.assertIsNone(parse_snapshot(document).merge_state_status)
                self.assertEqual(verdict(document), "defer")

    def test_conflicting_aliases_emit_cli_invalid_input_without_ready(self):
        for field, value in (("draft", True), ("checks", []), ("merge_state_status", "BLOCKED")):
            with self.subTest(field=field):
                document = snapshot()
                document[field] = value
                output = StringIO()
                with patch("sys.stdin", StringIO(json.dumps(document))), redirect_stdout(output):
                    code = main(["--input", "-", "--expected-head-sha", HEAD,
                                 "--expected-base-sha", BASE])
                report = json.loads(output.getvalue())
                self.assertEqual(code, EXIT_REJECT)
                self.assertEqual((report["verdict"], report["code"]), ("reject", "INVALID_INPUT"))
                self.assertFalse(report["merge_performed"])


if __name__ == "__main__":
    unittest.main()
