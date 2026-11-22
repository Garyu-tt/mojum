"""Untrusted model patch preflight is syntactic, exact-scope and fail-closed."""
import importlib.util
import io
import os
import subprocess
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "tools" / "spartan_patch_guard.py"
_spec = importlib.util.spec_from_file_location("spartan_patch_guard", MODULE)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

PATH = "src/mojum/aquarium.py"
EDIT = (f"diff --git a/{PATH} b/{PATH}\n"
        "index 0123456..abcdef0 100644\n"
        f"--- a/{PATH}\n+++ b/{PATH}\n"
        "@@ -1,2 +1,2 @@\n one\n-old\n+new\n")
CREATED = ("diff --git a/tests/test_new_case.py b/tests/test_new_case.py\n"
           "new file mode 100644\nindex 0000000..123abcd\n"
           "--- /dev/null\n+++ b/tests/test_new_case.py\n"
           "@@ -0,0 +1,2 @@\n+hello\n+world\n")


class PatchGuardTests(unittest.TestCase):
    def test_valid_edit_has_exact_digest_and_stats(self):
        result = guard.validate_patch(EDIT, [PATH])
        self.assertEqual(result["files"], [PATH])
        self.assertEqual((result["additions"], result["deletions"], result["hunks"]), (1, 1, 1))
        self.assertEqual(len(result["sha256"]), 64)

    def test_created_text_and_multiple_sections(self):
        result = guard.validate_patch(EDIT + CREATED, [PATH, "tests/test_new_case.py"])
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(result["additions"], 3)

    def test_scope_is_exact_and_mandatory(self):
        for grants in ([], ["src/mojum/"], ["tests/test_other.py"], [PATH, PATH]):
            with self.subTest(grants=grants), self.assertRaises(guard.PatchGuardError):
                guard.validate_patch(EDIT, grants)

    def test_paths_refuse_traversal_and_untrusted_roots(self):
        for path in ("src/mojum/../secrets.py", "/tmp/t.py", ".github/workflows/a.yml",
                     "tools/.git/config", "tools//x.py", "tools/a\\b.py", "src/mojum/é.py"):
            with self.subTest(path=path), self.assertRaises(guard.PatchGuardError):
                guard._safe_path(path)

    def test_new_file_must_be_regular_nonexecutable(self):
        for marker in ("new file mode 100755", "new file mode 120000", "deleted file mode 100644"):
            with self.subTest(marker=marker), self.assertRaises(guard.PatchGuardError):
                guard.validate_patch(CREATED.replace("new file mode 100644", marker),
                                     ["tests/test_new_case.py"])

    def test_rename_copy_and_binary_headers_refused(self):
        for extra in ("rename from other.py", "copy from a.py", "GIT binary patch", "old mode 100644"):
            with self.subTest(extra=extra), self.assertRaises(guard.PatchGuardError):
                guard.validate_patch(EDIT.replace("index 0123456", extra + "\nindex 0123456"), [PATH])

    def test_unquoted_headers_cannot_switch_path(self):
        changed = EDIT.replace(f"b/{PATH}\nindex", "b/tests/test_new_case.py\nindex")
        with self.assertRaises(guard.PatchGuardError):
            guard.validate_patch(changed, [PATH])

    def test_overlapping_or_reordered_hunks_are_rejected(self):
        first_hunk = "@@ -1,2 +1,2 @@\n one\n-old\n+new\n"
        for later_hunk in (
            first_hunk,  # Duplicate source/output lines.
            "@@ -3 +1 @@\n-old\n+new\n",  # New range moves backwards.
            "@@ -2 +4 @@\n-old\n+new\n",  # Old range overlaps.
        ):
            with self.subTest(later_hunk=later_hunk):
                with self.assertRaisesRegex(
                        guard.PatchGuardError, "overlapping or out-of-order"):
                    guard.validate_patch(EDIT + later_hunk, [PATH])

    def test_distinct_ordered_hunks_still_accepted(self):
        next_hunk = "@@ -4 +4 @@\n-old\n+new\n"
        result = guard.validate_patch(EDIT + next_hunk, [PATH])
        self.assertEqual(result["hunks"], 2)

    def test_stale_hunk_counts_are_rejected(self):
        for wrong in ("@@ -1,2 +1,3 @@", "@@ -1,1 +1,2 @@", "@@ -0,2 +1,2 @@"):
            with self.subTest(wrong=wrong), self.assertRaises(guard.PatchGuardError):
                guard.validate_patch(EDIT.replace("@@ -1,2 +1,2 @@", wrong), [PATH])

    def test_unbounded_or_non_ascii_hunk_coordinates_fail_closed(self):
        for coordinate in ("9" * 5000, "9" * 13, "\u0661"):
            with self.subTest(digits=len(coordinate)):
                malformed = EDIT.replace(
                    "@@ -1,2 +1,2 @@", f"@@ -{coordinate},2 +1,2 @@")
                with self.assertRaises(guard.PatchGuardError):
                    guard.validate_patch(malformed, [PATH])

    def test_no_newline_comment_refused(self):
        with self.assertRaises(guard.PatchGuardError):
            guard.validate_patch(EDIT + "\\ No newline at end of file\n", [PATH])

    def test_duplicate_file_section_refused(self):
        with self.assertRaises(guard.PatchGuardError):
            guard.validate_patch(EDIT + EDIT, [PATH])

    def test_unicode_line_separators_cannot_forge_git_hunk_lines(self):
        # splitlines() would treat Unicode NEL/LS/PS as new Git diff lines.
        for separator in ("\u0085", "\u2028", "\u2029"):
            with self.subTest(separator=repr(separator)):
                forged = CREATED.replace("+hello\n+world\n",
                                         "+hello" + separator + "+world\n")
                with self.assertRaises(guard.PatchGuardError):
                    guard.validate_patch(forged, ["tests/test_new_case.py"])

        # Valid Unicode inside one physically LF-delimited line stays one line.
        single = CREATED.replace(
            "@@ -0,0 +1,2 @@\n+hello\n+world\n",
            "@@ -0,0 +1 @@\n+hello\u2028world\n",
        )
        self.assertEqual(
            guard.validate_patch(single, ["tests/test_new_case.py"])["additions"], 1
        )

    def test_truncated_or_crlf_or_nul_or_surrogate_refused(self):
        for payload in (EDIT[:-1], EDIT.replace("\n", "\r\n"), EDIT + "\0", EDIT + "\ud800"):
            with self.subTest(payload=repr(payload[-20:])), self.assertRaises(guard.PatchGuardError):
                guard.validate_patch(payload, [PATH])

    def test_huge_patch_refused_before_parsing(self):
        with self.assertRaises(guard.PatchGuardError):
            guard.validate_patch(EDIT + "x" * guard.MAX_PATCH_BYTES, [PATH])

    def test_context_only_hunk_refused(self):
        test = EDIT.replace("@@ -1,2 +1,2 @@\n one\n-old\n+new\n", "@@ -1,1 +1,1 @@\n one\n")
        with self.assertRaises(guard.PatchGuardError):
            guard.validate_patch(test, [PATH])

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo")
                         and getattr(os, "O_NONBLOCK", 0),
                         "POSIX nonblocking FIFO support required")
    def test_cli_refuses_fifo_without_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            fifo = Path(td) / "patch.fifo"
            os.mkfifo(fifo)
            command = [sys.executable, str(MODULE), "--patch", str(fifo),
                       "--allow-path", PATH]
            run = subprocess.run(command, capture_output=True, text=True,
                                 check=False, timeout=5)
            self.assertEqual(run.returncode, 2)
            self.assertIn("patch input must be a regular file", run.stderr)

    @unittest.skipUnless(getattr(os, "O_NOFOLLOW", 0), "O_NOFOLLOW not supported")
    def test_cli_refuses_symlinked_patch_file(self):
        with tempfile.TemporaryDirectory() as td:
            patch = Path(td) / "real.diff"
            patch.write_text(EDIT, encoding="utf-8")
            link = Path(td) / "link.diff"
            link.symlink_to(patch)
            command = [sys.executable, str(MODULE), "--patch", str(link),
                       "--allow-path", PATH]
            run = subprocess.run(command, capture_output=True, text=True,
                                 check=False, timeout=5)
            self.assertEqual(run.returncode, 2)
            self.assertIn('"accepted": false', run.stderr)

    def test_cli_success_and_failure(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "patch.diff"
            path.write_text(EDIT, encoding="utf-8")
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(guard.main(["--patch", str(path), "--allow-path", PATH]), 0)
            self.assertIn('"accepted": true', out.getvalue())
            with redirect_stderr(io.StringIO()) as err:
                self.assertEqual(guard.main(["--patch", str(path), "--allow-path", "docs/note.md"]), 2)
            self.assertIn('"accepted": false', err.getvalue())


if __name__ == "__main__":
    unittest.main()
