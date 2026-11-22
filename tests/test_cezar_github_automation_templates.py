"""Hermetic schema and fail-closed default checks for Cezar GitHub templates."""
import json
from pathlib import Path
import unittest

BASE = Path(__file__).resolve().parents[1] / "ops" / "cezar" / "automations"


class CezarGithubTemplateTests(unittest.TestCase):
    def test_templates_are_paused_import_inputs_and_never_merge(self):
        files = sorted(BASE.glob("*.json"))
        self.assertEqual(len(files), 2)
        kinds = set()
        for f in files:
            with self.subTest(file=f.name):
                data = json.loads(f.read_text(encoding="utf-8"))
                self.assertEqual(data["kind"], "github")
                self.assertNotIn("enabled", data)
                self.assertNotIn("credentials", data)
                self.assertLessEqual(data["filters"]["maxRecords"], 5)
                self.assertGreaterEqual(data["intervalSeconds"], 300)
                self.assertEqual(data["task"]["runner"], "opencode")
                self.assertTrue(data["task"]["worktree"])
                self.assertFalse(data["task"]["autonomous"])
                self.assertFalse(data["task"]["generateFollowups"])
                self.assertNotIn("merge", data["task"].keys())
                kinds.update(data["events"])
        self.assertEqual(kinds, {"issue.labeled", "pull_request.opened"})

    def test_issue_intake_is_opt_in_by_changed_label(self):
        data = json.loads((BASE / "issue-labeled.json").read_text(encoding="utf-8"))
        self.assertEqual(data["filters"]["changedLabels"], ["cezar-ready"])
        self.assertIn("cezar-hold", data["filters"]["excludeLabels"])
        self.assertEqual(data["task"]["variants"], 1)

    def test_pr_triage_asks_for_exact_ci_and_refuses_merge(self):
        data = json.loads((BASE / "pr-opened-qa.json").read_text(encoding="utf-8"))
        prompt = data["task"]["prompt"].lower()
        self.assertIn("head/base", prompt)
        self.assertIn("do not execute", prompt)
        self.assertIn("do not execute untrusted pr content", prompt)
        self.assertIn("merge", prompt)


if __name__ == "__main__":
    unittest.main()
