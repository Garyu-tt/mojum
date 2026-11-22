"""Prepare a task-specific Git worktree from a pinned commit.

Only use with a trusted repository and trusted Git configuration. This does not
sandbox Git filters, apply model patches, execute tests, or publish PRs.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess

TASK = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z", re.ASCII)
SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)


class IsolationError(RuntimeError):
    pass


def prepare(repo: Path, root: Path, task: str, base_sha: str) -> Path:
    if not TASK.fullmatch(task) or not SHA.fullmatch(base_sha):
        raise IsolationError("invalid task or base SHA")
    raw_root = Path(root).absolute()
    if any(part.is_symlink() for part in (raw_root, *raw_root.parents)):
        raise IsolationError("symlink in worktree root path refused")
    repo = Path(repo).resolve(strict=True)
    root = Path(root).resolve(strict=True)
    if root == repo or root in repo.parents or repo in root.parents:
        raise IsolationError("worktree root must be separate from repository")
    if not root.is_dir():
        raise IsolationError("invalid worktree root")
    dest = root / task
    if dest.exists() or dest.is_symlink():
        raise IsolationError("destination already exists")
    branch = f"spartan/{task}"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
           "GIT_OPTIONAL_LOCKS": "0"}

    def git(*args):
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
            check=True, capture_output=True, text=True, timeout=20, env=env)

    try:
        git("rev-parse", "--is-inside-work-tree")
        git("cat-file", "-e", f"{base_sha}^{{commit}}")
        git("worktree", "add", "--no-checkout", "-b", branch, str(dest), base_sha)
        git("-C", str(dest), "checkout", "--force", branch)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise IsolationError("git worktree preparation failed; inspect partial worktree") from exc
    return dest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--base-sha", required=True)
    args = parser.parse_args()
    try:
        print(prepare(args.repo, args.root, args.task, args.base_sha))
    except IsolationError as exc:
        parser.exit(2, f"refused: {exc}\n")


if __name__ == "__main__":
    main()
