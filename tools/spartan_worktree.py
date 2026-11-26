"""Prepare a task-specific Git worktree from a pinned commit.

Only use with a trusted repository and trusted Git configuration, under an
operator-owned mode-0700 worktree root. This does not sandbox Git filters,
apply model patches, execute tests, or publish PRs.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import stat

TASK = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z", re.ASCII)
SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)


class IsolationError(RuntimeError):
    pass



def _require_private_worktree_root(root: Path) -> None:
    """Refuse writable/replaceable POSIX worktree roots before Git writes.

    A worker can race a pathname preflight if a different local account can
    replace its directory or an ancestor. Sticky ancestors such as /tmp are
    permitted only when the root itself is owner-controlled and private.
    Same-UID attackers and concurrent mutations are outside this preflight.
    """
    if os.name != "posix":
        return
    for depth, directory in enumerate((root, *root.parents)):
        try:
            info = os.lstat(directory)
        except OSError as exc:
            raise IsolationError("cannot inspect worktree root directory") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise IsolationError("worktree root path contains a non-directory")
        if depth == 0:
            # Worktrees may contain private source and generated artifacts.
            # The root must be inaccessible to other users, not just unwritable.
            unsafe = info.st_uid != os.geteuid() or bool(info.st_mode & 0o077)
        else:
            unsafe = (info.st_uid not in (0, os.geteuid())
                      or (bool(info.st_mode & 0o022)
                          and not bool(info.st_mode & stat.S_ISVTX)))
        if unsafe:
            raise IsolationError("worktree root directory chain is not owner-controlled")


def prepare(repo: Path, root: Path, task: str, base_sha: str) -> Path:
    if not TASK.fullmatch(task) or not SHA.fullmatch(base_sha):
        raise IsolationError("invalid task or base SHA")
    # Reject parent traversal before checking ancestors: symlink/.. can
    # resolve differently from lexical Path.parents and bypass the guard.
    if ".." in Path(root).parts:
        raise IsolationError("parent traversal in worktree root refused")
    raw_root = Path(root).absolute()
    if any(part.is_symlink() for part in (raw_root, *raw_root.parents)):
        raise IsolationError("symlink in worktree root path refused")
    repo = Path(repo).resolve(strict=True)
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise IsolationError("invalid worktree root")
    _require_private_worktree_root(root)
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
        # Git implementations such as MSYS2 may print an absolute path in
        # their own namespace. --show-cdup is relative to -C and remains
        # meaningful to the Python process on both Windows and POSIX.
        cdup = git("rev-parse", "--show-cdup").stdout.rstrip("\r\n")
        if not re.fullmatch(r"(?:\.\./)*", cdup):
            raise IsolationError("unexpected Git top-level relative path")
        top = (repo / cdup).resolve(strict=True)
        if root == top or root in top.parents or top in root.parents:
            raise IsolationError("worktree root must be separate from repository")
        # ^0 peels a commit (or a tag to a commit) without braces, which
        # MSYS2 argv processing may strip before Git sees them.
        git("cat-file", "-e", f"{base_sha}^0")
        git("worktree", "add", "--no-checkout", "-b", branch, str(dest), base_sha)
        git("-C", str(dest), "checkout", "--force", branch)
    except IsolationError:
        raise
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
