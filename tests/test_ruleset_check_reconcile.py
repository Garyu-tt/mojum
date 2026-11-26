"""Adversarial tests for ruleset/check-run reconciliation."""
import copy
import unittest

from mojum.ruleset_check_reconcile import reconcile_ruleset_checks

SHA = "a" * 40
APP = 15368


def fixture(count=2):
    contexts = [f"required-{i}" for i in range(count)]
    ruleset = {
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
        "rules": [{
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [
                    {"context": name, "integration_id": APP} for name in contexts
                ],
            },
        }],
    }
    runs = [{
        "name": name, "head_sha": SHA, "status": "completed",
        "conclusion": "success", "app": {"id": APP},
    } for name in contexts]
    return ruleset, {"total_count": len(runs), "check_runs": runs}


class RulesetCheckReconcileTests(unittest.TestCase):
    def evaluate(self, ruleset=None, snapshot=None):
        default_ruleset, default_snapshot = fixture()
        return reconcile_ruleset_checks(
            default_ruleset if ruleset is None else ruleset,
            default_snapshot if snapshot is None else snapshot,
            SHA,
        )

    def test_all_exact_required_app_checks_pass(self):
        result = self.evaluate()
        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(result.passed, ("required-0", "required-1"))
        self.assertEqual(len(result.receipt_sha256), 64)

    def test_check_context_and_run_name_may_contain_spaces(self):
        ruleset, snapshot = fixture(count=1)
        context = "Build and Test / Windows"
        ruleset["rules"][0]["parameters"]["required_status_checks"][0]["context"] = context
        snapshot["check_runs"][0]["name"] = context
        result = reconcile_ruleset_checks(ruleset, snapshot, SHA)
        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(result.passed, (context,))

    def test_check_on_different_commit_is_missing(self):
        _, snap = fixture()
        snap["check_runs"][0]["head_sha"] = "b" * 40
        result = self.evaluate(snapshot=snap)
        self.assertEqual(result.verdict, "HOLD")
        self.assertEqual(result.missing, ("required-0",))

    def test_wrong_app_id_does_not_satisfy_required_check(self):
        _, snap = fixture()
        snap["check_runs"][0]["app"]["id"] = APP + 1
        result = self.evaluate(snapshot=snap)
        self.assertEqual(result.verdict, "HOLD")
        self.assertEqual(result.missing, ("required-0",))

    def test_legacy_or_missing_app_does_not_satisfy_required_check(self):
        _, snap = fixture()
        snap["check_runs"][0]["app"] = None
        result = self.evaluate(snapshot=snap)
        self.assertEqual(result.missing, ("required-0",))

    def test_pending_check_holds(self):
        _, snap = fixture()
        snap["check_runs"][0]["status"] = "in_progress"
        snap["check_runs"][0]["conclusion"] = None
        result = self.evaluate(snapshot=snap)
        self.assertEqual(result.pending, ("required-0",))
        self.assertEqual(result.verdict, "HOLD")

    def test_unknown_or_null_conclusion_holds(self):
        for conclusion in (None, "unexpected"):
            with self.subTest(conclusion=conclusion):
                _, snap = fixture()
                snap["check_runs"][0]["conclusion"] = conclusion
                self.assertIn("required-0", self.evaluate(snapshot=snap).pending)

    def test_failure_rejects_only_after_all_other_evidence_is_complete(self):
        _, snap = fixture()
        snap["check_runs"][0]["conclusion"] = "failure"
        result = self.evaluate(snapshot=snap)
        self.assertEqual(result.verdict, "REJECT")
        self.assertEqual(result.failed, ("required-0",))

    def test_skipped_check_fails_closed(self):
        _, snap = fixture()
        snap["check_runs"][0]["conclusion"] = "skipped"
        self.assertEqual(self.evaluate(snapshot=snap).verdict, "REJECT")

    def test_partial_pagination_holds_even_when_visible_checks_pass(self):
        _, snap = fixture()
        snap["total_count"] += 1
        result = self.evaluate(snapshot=snap)
        self.assertEqual(result.verdict, "HOLD")
        self.assertIn("check_runs_page_incomplete", result.reasons)

    def test_duplicate_attempts_are_ambiguous(self):
        _, snap = fixture()
        snap["check_runs"].append(copy.deepcopy(snap["check_runs"][0]))
        snap["total_count"] += 1
        result = self.evaluate(snapshot=snap)
        self.assertEqual(result.verdict, "HOLD")
        self.assertEqual(result.ambiguous, ("required-0",))

    def test_inactive_ruleset_holds(self):
        ruleset, _ = fixture()
        ruleset["enforcement"] = "disabled"
        self.assertIn("ruleset_not_active", self.evaluate(ruleset=ruleset).reasons)

    def test_ruleset_must_include_main(self):
        ruleset, _ = fixture()
        ruleset["conditions"]["ref_name"]["include"] = ["refs/heads/release"]
        self.assertIn("ruleset_does_not_target_main", self.evaluate(ruleset=ruleset).reasons)

    def test_excluded_main_is_not_accepted(self):
        ruleset, _ = fixture()
        ruleset["conditions"]["ref_name"]["exclude"] = ["refs/heads/main"]
        self.assertIn("ruleset_does_not_target_main", self.evaluate(ruleset=ruleset).reasons)

    def test_non_strict_required_check_rule_holds(self):
        ruleset, _ = fixture()
        ruleset["rules"][0]["parameters"]["strict_required_status_checks_policy"] = False
        self.assertIn("strict_required_checks_disabled", self.evaluate(ruleset=ruleset).reasons)

    def test_duplicate_required_context_is_rejected(self):
        ruleset, _ = fixture()
        ruleset["rules"][0]["parameters"]["required_status_checks"].append(
            {"context": "required-0", "integration_id": APP + 1}
        )
        with self.assertRaises(ValueError):
            self.evaluate(ruleset=ruleset)

    def test_bool_is_not_an_integration_id(self):
        ruleset, _ = fixture()
        ruleset["rules"][0]["parameters"]["required_status_checks"][0]["integration_id"] = True
        with self.assertRaises(ValueError):
            self.evaluate(ruleset=ruleset)

    def test_empty_required_checks_are_rejected(self):
        ruleset, _ = fixture()
        ruleset["rules"][0]["parameters"]["required_status_checks"] = []
        with self.assertRaises(ValueError):
            self.evaluate(ruleset=ruleset)

    def test_wrong_sha_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            reconcile_ruleset_checks(fixture()[0], fixture()[1], "A" * 40)

    def test_duplicate_required_rule_is_rejected(self):
        ruleset, _ = fixture()
        ruleset["rules"].append(copy.deepcopy(ruleset["rules"][0]))
        with self.assertRaises(ValueError):
            self.evaluate(ruleset=ruleset)


if __name__ == "__main__":
    unittest.main()
