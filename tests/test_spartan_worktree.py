"""Offline worktree tests, using a temporary trusted Git repository."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "tools" / "spartan_worktree.py"
spec = importlib.util.spec_from_file_location("spartan_worktree", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class WorktreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / "repo"
        self.root = self.base / "worktrees"
        self.repo.mkdir()
        self.root.mkdir()

        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(self.repo), *args], text=True).strip()

        git("init", "-q")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-q", "--allow-empty", "-m", "initial")
        self.sha = git("rev-parse", "HEAD")

    def test_creates_isolated_checkout(self):
        dest = mod.prepare(self.repo, self.root, "issue-478-worker-2", self.sha)
        self.assertEqual(dest, self.root / "issue-478-worker-2")
        head = subprocess.check_output(
            ["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(head, self.sha)
        attached = subprocess.check_output(
            ["git", "-C", str(dest), "symbolic-ref", "--short", "HEAD"],
            text=True).strip()
        self.assertEqual(attached, "spartan/issue-478-worker-2")
        subprocess.check_call(
            ["git", "-C", str(dest), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid",
             "commit", "-q", "--allow-empty", "-m", "worker change"])
        branch_sha = subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse",
             "refs/heads/spartan/issue-478-worker-2"], text=True).strip()
        worker_sha = subprocess.check_output(
            ["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(branch_sha, worker_sha)
        self.assertNotEqual(worker_sha, self.sha)

    def test_rejects_duplicate(self):
        mod.prepare(self.repo, self.root, "task-1", self.sha)
        with self.assertRaises(mod.IsolationError):
            mod.prepare(self.repo, self.root, "task-1", self.sha)

    def test_rejects_existing_branch_without_touching_it(self):
        # A worker must not adopt another worker's existing branch, even
        # when the requested destination is still empty.
        subprocess.check_call(
            ["git", "-C", str(self.repo), "branch", "spartan/task-occupied", self.sha])
        with self.assertRaises(mod.IsolationError):
            mod.prepare(self.repo, self.root, "task-occupied", self.sha)
        self.assertFalse((self.root / "task-occupied").exists())
        branch_sha = subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse",
             "refs/heads/spartan/task-occupied"], text=True).strip()
        self.assertEqual(branch_sha, self.sha)

    def test_rejects_symlink_destination_without_touching_target(self):
        outside = self.base / "outside"
        outside.mkdir()
        alias = self.root / "task-linked"
        try:
            alias.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks unavailable")
        with self.assertRaises(mod.IsolationError):
            mod.prepare(self.repo, self.root, "task-linked", self.sha)
        self.assertEqual(list(outside.iterdir()), [])

    def test_rejects_invalid_identifiers(self):
        for task in ("../escape", "UPPER", "a/b", "-bad", "x" * 49):
            with self.subTest(task=task), self.assertRaises(mod.IsolationError):
                mod.prepare(self.repo, self.root, task, self.sha)
        with self.assertRaises(mod.IsolationError):
            mod.prepare(self.repo, self.root, "task", "main")

    def test_rejects_nested_root(self):
        with self.assertRaises(mod.IsolationError):
            mod.prepare(self.repo, self.repo, "task", self.sha)

    def test_rejects_root_inside_actual_git_toplevel_from_subdirectory(self):
        # Passing a repository subdirectory must not weaken the containment
        # check: Git -C walks up to the real repository root.
        subdir = self.repo / "nested"
        subdir.mkdir()
        unsafe_root = self.repo / "worktrees"
        unsafe_root.mkdir()
        with self.assertRaises(mod.IsolationError):
            mod.prepare(subdir, unsafe_root, "task", self.sha)
        self.assertFalse((unsafe_root / "task").exists())
        self.assertFalse((self.repo / ".git" / "worktrees").exists())

    def test_rejects_symlinked_root_ancestor(self):
        actual_parent = self.base / "actual-parent"
        actual_parent.mkdir()
        (actual_parent / "worktrees").mkdir()
        alias = self.base / "alias-parent"
        try:
            alias.symlink_to(actual_parent, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks unavailable")
        with self.assertRaises(mod.IsolationError):
            mod.prepare(self.repo, alias / "worktrees", "task", self.sha)
        self.assertFalse((actual_parent / "worktrees" / "task").exists())

    def test_rejects_parent_traversal_before_symlink_resolution(self):
        outside = self.base / "outside"
        outside.mkdir()
        alias = self.base / "alias-parent"
        try:
            alias.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks unavailable")
        # The lexical ancestor chain does not contain alias-parent itself.
        # POSIX resolves alias-parent/.. after following the symlink.
        tricky_root = alias / ".." / "worktrees"
        with self.assertRaises(mod.IsolationError):
            mod.prepare(self.repo, tricky_root, "task", self.sha)
        self.assertFalse((self.root / "task").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX directory owner/mode gate")
    def test_rejects_world_writable_worktree_root(self):
        self.root.chmod(0o777)
        with self.assertRaisesRegex(mod.IsolationError, "owner-controlled"):
            mod.prepare(self.repo, self.root, "task-unsafe", self.sha)
        self.assertFalse((self.root / "task-unsafe").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX directory owner/mode gate")
    def test_rejects_writable_ancestor_even_with_private_root(self):
        shared = self.base / "nonsticky-shared"
        shared.mkdir()
        shared.chmod(0o777)
        private = shared / "private"
        private.mkdir(mode=0o700)
        with self.assertRaisesRegex(mod.IsolationError, "owner-controlled"):
            mod.prepare(self.repo, private, "task-unsafe-parent", self.sha)
        self.assertFalse((private / "task-unsafe-parent").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX sticky ancestor handling")
    def test_allows_sticky_ancestor_with_private_root(self):
        shared = self.base / "sticky-shared"
        shared.mkdir()
        shared.chmod(0o1777)
        private = shared / "private"
        private.mkdir(mode=0o700)
        dest = mod.prepare(self.repo, private, "task-sticky-ok", self.sha)
        self.assertEqual(dest, private / "task-sticky-ok")
        self.assertTrue((dest / ".git").exists())

    def test_rejects_unknown_commit(self):
        with self.assertRaises(mod.IsolationError):
            mod.prepare(self.repo, self.root, "task", "f" * 40)


if __name__ == "__main__":
    unittest.main()
