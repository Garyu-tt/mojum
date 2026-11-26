"""Regression tests for provenance labels in the R0.14 ABI gate.

The cross-branch harness reports results for immutable historical checkouts.
These tests use a disposable real Git repository so a manually supplied
wrong ref cannot be accepted under a trusted #112/#115 label.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from qa_r014_cross_branch_journal import assert_pinned_checkout


class PinnedCheckoutProvenanceTests(unittest.TestCase):
    MARKER = "src/mojum/developmental_policy_v2.py"

    def _checkout(self):
        temp = tempfile.TemporaryDirectory(prefix="mojum-r014-provenance-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / self.MARKER).parent.mkdir(parents=True)
        (root / self.MARKER).write_text("# disposable marker\n", encoding="utf-8")
        commands = [
            ["git", "init", "--quiet", str(root)],
            ["git", "-C", str(root), "config", "user.name", "Mojum QA"],
            ["git", "-C", str(root), "config", "user.email", "qa@localhost"],
            ["git", "-C", str(root), "add", self.MARKER],
            ["git", "-C", str(root), "commit", "--quiet", "-m", "marker"],
        ]
        for command in commands:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=15, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        actual = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip()
        return root, actual

    @unittest.skipUnless(shutil.which("git"), "git is required by the ABI gate")
    def test_exact_head_is_accepted(self):
        root, actual = self._checkout()
        assert_pinned_checkout(root, actual, self.MARKER)

    @unittest.skipUnless(shutil.which("git"), "git is required by the ABI gate")
    def test_wrong_head_is_rejected_before_labeling_results(self):
        root, actual = self._checkout()
        wrong = "0" * len(actual)
        with self.assertRaisesRegex(AssertionError, "differs from pinned source"):
            assert_pinned_checkout(root, wrong, self.MARKER)


    @unittest.skipUnless(shutil.which("git"), "git is required by the ABI gate")
    def test_dirty_tracked_source_is_rejected_with_matching_head(self):
        root, actual = self._checkout()
        (root / self.MARKER).write_text("# changed without a commit\\n", encoding="utf-8")
        with self.assertRaisesRegex(AssertionError, "tracked or untracked local changes"):
            assert_pinned_checkout(root, actual, self.MARKER)

    @unittest.skipUnless(shutil.which("git"), "git is required by the ABI gate")
    def test_untracked_source_is_rejected_with_matching_head(self):
        root, actual = self._checkout()
        (root / "src/mojum/foreign_module.py").write_text(
            "# untracked injected module\\n", encoding="utf-8",
        )
        with self.assertRaisesRegex(AssertionError, "tracked or untracked local changes"):
            assert_pinned_checkout(root, actual, self.MARKER)


if __name__ == "__main__":
    unittest.main()
