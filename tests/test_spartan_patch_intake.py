"""Offline adversarial tests for private Spartan patch candidate intake."""
import io
import json
import os
from pathlib import Path
import hashlib
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from tools import spartan_patch_intake as intake

TASK = "spartan-test-patch-01"
SCOPE = "aquarium.test"
PATH = "tests/test_spartan_intake_example.py"
PATCH = (
    f"diff --git a/{PATH} b/{PATH}\n"
    "new file mode 100644\n"
    "index 0000000..123abcd\n"
    "--- /dev/null\n"
    f"+++ b/{PATH}\n"
    "@@ -0,0 +1,2 @@\n"
    "+def test_simple_math():\n"
    "+    assert 1 + 1 == 2\n"
)


class PatchIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "private"
        self.folder.mkdir(mode=0o700)
        self.file = self.folder / (
            hashlib.sha256(TASK.encode()).hexdigest()[:24] + "-attempt-1.json")
        self.artifact = {
            "schema": intake.SCHEMA,
            "task_id": TASK,
            "scope": SCOPE,
            "attempt": 1,
            "reviewed": False,
            "patch_applied": False,
            "tests_run": False,
            "model_calls": [
                {"model": "QwenCoder3B", "port": 48083, "proposal": PATCH}
            ],
        }
        self.write()

    def write(self, raw=None):
        if raw is None:
            raw = json.dumps(self.artifact).encode()
        self.file.write_bytes(raw)
        self.file.chmod(0o600)

    def check(self, **override):
        kw = dict(private_dir=self.folder, task_id=TASK, scope=SCOPE,
                  attempt=1, allowed_paths=[PATH])
        kw.update(override)
        return intake.assess_private_artifact(**kw)

    def test_valid_complete_git_diff_is_only_next_stage_candidate(self):
        report = self.check()
        self.assertEqual(report["syntax_and_scope_candidates"], 1)
        self.assertEqual(report["not_patch_candidates"], 0)
        self.assertEqual(report["candidates"][0]["file_count"], 1)
        self.assertEqual(report["candidates"][0]["patch_sha256"],
                         hashlib.sha256(PATCH.encode()).hexdigest())
        self.assertTrue(report["only_read_only_preflight"])
        for field in ("safe_to_execute", "tests_run", "github_published",
                      "accepted_by_independent_qa"):
            self.assertFalse(report[field])
        self.assertNotIn("test_simple_math", json.dumps(report))

    def test_markdown_and_pseudocode_never_turn_into_patches(self):
        for proposal in (
            "### Proposal\n\nPlease add this test",
            "~~~~diff\n" + PATCH + "~~~~\n",
            "Here is the diff:\n" + PATCH,
            "~~~diff\n" + PATCH + "~~~\n",
            "def test_simple_math(): assert 1+1 == 2",
        ):
            with self.subTest(start=proposal[:30]):
                self.artifact["model_calls"][0]["proposal"] = proposal
                self.write()
                report = self.check()
                self.assertEqual(report["syntax_and_scope_candidates"], 0)
                self.assertEqual(report["candidates"][0]["reason"],
                                 "not_an_exact_git_diff")

    def test_out_of_grant_or_malformed_git_diff_is_rejected(self):
        for change in (
            PATCH.replace(f"a/{PATH} b/{PATH}", "a/tests/other.py b/tests/other.py"),
            PATCH.replace("@@ -0,0 +1,2 @@", "@@ -0,0 +1,3 @@"),
            PATCH.replace("new file mode 100644", "new file mode 100755"),
        ):
            self.artifact["model_calls"][0]["proposal"] = change
            self.write()
            report = self.check()
            self.assertEqual(report["syntax_and_scope_candidates"], 0)
            self.assertEqual(report["candidates"][0]["reason"],
                             "git_diff_rejected_by_scope_or_syntax_guard")

    def test_exact_grant_required_even_for_valid_diff(self):
        report = self.check(allowed_paths=["tests/other.py"])
        self.assertEqual(report["syntax_and_scope_candidates"], 0)

    def test_clone_tracks_each_independent_candidate(self):
        self.artifact["model_calls"].append(
            {"model": "Qwen3_1p7B", "port": 48082,
             "proposal": "### Not a patch\n"})
        self.write()
        report = self.check()
        self.assertEqual((report["syntax_and_scope_candidates"],
                          report["not_patch_candidates"]), (1, 1))
        self.assertEqual(len(report["candidates"]), 2)

    def test_task_or_scope_or_attempt_mismatch_fails_closed(self):
        for kw in ({"task_id": "other"},
                   {"scope": "other"},
                   {"attempt": 2},
                   {"attempt": True}):
            with self.subTest(kw=kw), self.assertRaises(intake.IntakeError):
                self.check(**kw)

    def test_artifact_does_not_self_approve_or_claim_execution(self):
        for field in ("reviewed", "patch_applied", "tests_run"):
            self.artifact[field] = True
            self.write()
            with self.subTest(field=field), self.assertRaises(intake.IntakeError):
                self.check()
            self.artifact[field] = False

    def test_duplicate_keys_and_nonfinite_values_fail_closed(self):
        raw = json.dumps(self.artifact).replace(
            '"task_id": ', '"task_id": "forged", "task_id": ', 1).encode()
        self.write(raw)
        with self.assertRaises(intake.IntakeError):
            self.check()
        self.write(json.dumps(self.artifact).replace(
            '"attempt": 1', '"attempt": NaN').encode())
        with self.assertRaises(intake.IntakeError):
            self.check()

    def test_untrusted_call_metadata_fails_closed(self):
        bad = [
            [], [{}, {}], [dict(self.artifact["model_calls"][0], model="")],
            [dict(self.artifact["model_calls"][0], port=True)],
        ]
        for calls in bad:
            with self.subTest(calls=calls):
                self.artifact["model_calls"] = calls
                self.write()
                with self.assertRaises(intake.IntakeError):
                    self.check()

    def test_artifact_is_bounded(self):
        self.write(b"x" * (intake.MAX_ARTIFACT_BYTES + 1))
        with self.assertRaises(intake.IntakeError):
            self.check()

    def test_owner_private_file_and_directory_required(self):
        self.file.chmod(0o644)
        with self.assertRaises(intake.IntakeError):
            self.check()
        self.file.chmod(0o600)
        self.folder.chmod(0o755)
        with self.assertRaises(intake.IntakeError):
            self.check()

    @unittest.skipUnless(os.name == "posix", "POSIX hardened file descriptors")
    def test_symlink_file_and_ancestor_rejected(self):
        replacement = self.folder / "real.json"
        self.file.rename(replacement)
        self.file.symlink_to(replacement)
        with self.assertRaises(intake.IntakeError):
            self.check()
        self.file.unlink()
        replacement.rename(self.file)
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(self.folder, target_is_directory=True)
        with self.assertRaises(intake.IntakeError):
            self.check(private_dir=alias)

    @unittest.skipUnless(os.name == "posix", "POSIX hard link check")
    def test_hardlinked_artifact_refused(self):
        alias = self.folder / "other.json"
        os.link(self.file, alias)
        with self.assertRaises(intake.IntakeError):
            self.check()

    def test_no_raw_model_text_in_cli_summary(self):
        self.artifact["model_calls"][0]["proposal"] = "PRIVATE_MODEL_DATA"
        self.write()
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = intake.main([
                "--private-dir", str(self.folder),
                "--task-id", TASK, "--scope", SCOPE, "--attempt", "1",
                "--allow-path", PATH,
            ])
        self.assertEqual(exit_code, 0)
        self.assertNotIn("PRIVATE_MODEL_DATA", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["not_patch_candidates"], 1)

    def test_cli_error_is_sanitized(self):
        self.file.chmod(0o644)
        errors = io.StringIO()
        with redirect_stderr(errors):
            exit_code = intake.main([
                "--private-dir", str(self.folder),
                "--task-id", TASK, "--scope", SCOPE, "--attempt", "1",
                "--allow-path", PATH,
            ])
        self.assertEqual(exit_code, 2)
        self.assertIn("private_intake_rejected", errors.getvalue())
        self.assertNotIn(str(self.folder), errors.getvalue())


if __name__ == "__main__":
    unittest.main()
