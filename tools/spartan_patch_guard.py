#!/usr/bin/env python3
"""Fail-closed syntax and scope gate for untrusted Spartan unified diffs.

This is NOT a sandbox, a patch applier, a code reviewer, or CI acceptance.
The trusted executor must additionally inspect the worktree, apply the patch
with Git's safety checks, and run tests without network or credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

MAX_PATCH_BYTES = 256 * 1024
MAX_FILES = 16
MAX_CHANGED_LINES = 1200
MAX_HUNKS = 128
SAFE_ROOTS = ("src/mojum/", "tests/", "docs/", "tools/")
_PATH_RE = re.compile(r"[A-Za-z0-9_./-]+\Z")
_INDEX_RE = re.compile(r"index [0-9a-f]{7,64}\.\.[0-9a-f]{7,64}(?: 100644)?\Z")
_HUNK_RE = re.compile(r"@@ -([0-9]+)(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@(?: [^\r\n]{0,160})?\Z")


class PatchGuardError(ValueError):
    """An untrusted diff does not meet the bounded textual patch contract."""


def _hunk_position(raw: str) -> int:
    # Limit decimal conversion work before int() sees untrusted attacker text.
    # A 256 KiB patch can otherwise contain 5,000-digit numbers, which raise
    # a bare ValueError on Python's integer-string conversion limit.
    if len(raw) > 12:
        raise PatchGuardError("hunk coordinate exceeds supported range")
    try:
        return int(raw)
    except (ValueError, OverflowError) as exc:
        raise PatchGuardError("invalid hunk coordinate") from exc


def _safe_path(path: str) -> str:
    if not isinstance(path, str) or len(path) > 180 or not _PATH_RE.fullmatch(path):
        raise PatchGuardError("unsupported patch path")
    parts = path.split("/")
    if (path.startswith("/") or any(p in ("", ".", "..") for p in parts)
            or any(p.startswith(".git") for p in parts)
            or not path.startswith(SAFE_ROOTS)):
        raise PatchGuardError("unsafe or out-of-root patch path")
    return path


def validate_patch(patch: str, allowed_paths: list[str] | tuple[str, ...]) -> dict:
    """Check a single Git-style textual diff against exact trusted path grants.

    The input must be the entire diff, not a filtered or truncated preview.
    No Git operations, filesystem reads, command execution, or network calls.
    """
    if type(patch) is not str:
        raise PatchGuardError("patch must be text")
    try:
        encoded = patch.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise PatchGuardError("invalid patch unicode") from exc
    if (not encoded or len(encoded) > MAX_PATCH_BYTES or b"\x00" in encoded
            or b"\r" in encoded or not encoded.endswith(b"\n")):
        raise PatchGuardError("patch size or encoding invalid")
    if not allowed_paths or len(allowed_paths) > MAX_FILES:
        raise PatchGuardError("at least one bounded exact path grant required")
    grants = [_safe_path(p) for p in allowed_paths]
    if len(set(grants)) != len(grants):
        raise PatchGuardError("duplicate path grants")
    allowed = set(grants)
    lines = patch.splitlines()
    files = []
    additions = deletions = hunks = 0
    i = 0
    while i < len(lines):
        head = lines[i]
        if not head.startswith("diff --git a/"):
            raise PatchGuardError("expected Git file header")
        m = re.fullmatch(r"diff --git a/(\S+) b/(\S+)", head)
        if not m or m.group(1) != m.group(2):
            raise PatchGuardError("noncanonical file header or rename")
        path = _safe_path(m.group(1))
        if path not in allowed:
            raise PatchGuardError("ungranted patch path: " + path)
        if path in files:
            raise PatchGuardError("duplicate file section")
        files.append(path)
        if len(files) > MAX_FILES:
            raise PatchGuardError("too many changed files")
        i += 1
        new_file = False
        got_index = False
        while i < len(lines) and not lines[i].startswith("--- "):
            line = lines[i]
            if line == "new file mode 100644" and not new_file and not got_index:
                new_file = True
            elif _INDEX_RE.fullmatch(line) and not got_index:
                got_index = True
            else:
                raise PatchGuardError("forbidden Git metadata or missing old-file marker")
            i += 1
        if not got_index or i + 1 >= len(lines):
            raise PatchGuardError("missing index or file markers")
        expected_old = "/dev/null" if new_file else "a/" + path
        if lines[i] != "--- " + expected_old or lines[i + 1] != "+++ b/" + path:
            raise PatchGuardError("file markers do not match declared path")
        i += 2
        file_hunks = 0
        last_old_end = 0
        last_new_end = 0
        while i < len(lines) and lines[i].startswith("@@ "):
            match = _HUNK_RE.fullmatch(lines[i])
            if not match:
                raise PatchGuardError("malformed hunk header")
            old_start, old_count, new_start, new_count = (
                _hunk_position(match[1]), _hunk_position(match[2] or "1"),
                _hunk_position(match[3]), _hunk_position(match[4] or "1")
            )
            if (new_file and (old_start != 0 or old_count != 0)) or (
                not new_file and (old_start < 1 or new_start < 1)
            ):
                raise PatchGuardError("invalid hunk positions")
            if old_start < last_old_end or new_start < last_new_end:
                raise PatchGuardError("overlapping or out-of-order hunks")
            last_old_end = old_start + old_count
            last_new_end = new_start + new_count
            hunks += 1
            file_hunks += 1
            if hunks > MAX_HUNKS:
                raise PatchGuardError("too many hunks")
            i += 1
            remaining_old, remaining_new = old_count, new_count
            changed_here = 0
            while remaining_old or remaining_new:
                if i >= len(lines):
                    raise PatchGuardError("truncated hunk")
                line = lines[i]
                if not line or line[0] not in (" ", "+", "-"):
                    raise PatchGuardError("invalid hunk body")
                if line[0] == " ":
                    remaining_old -= 1
                    remaining_new -= 1
                elif line[0] == "+":
                    additions += 1
                    changed_here += 1
                    remaining_new -= 1
                else:
                    deletions += 1
                    changed_here += 1
                    remaining_old -= 1
                if remaining_old < 0 or remaining_new < 0:
                    raise PatchGuardError("hunk exceeds declared line counts")
                if additions + deletions > MAX_CHANGED_LINES:
                    raise PatchGuardError("patch exceeds changed-line budget")
                i += 1
            if not changed_here:
                raise PatchGuardError("context-only hunk refused")
        if not file_hunks:
            raise PatchGuardError("file section without hunks")
        if i < len(lines) and not lines[i].startswith("diff --git a/"):
            raise PatchGuardError("unexpected trailing diff content")
    if not files:
        raise PatchGuardError("empty diff")
    return {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "files": files,
        "file_count": len(files),
        "hunks": hunks,
        "additions": additions,
        "deletions": deletions,
        "bytes": len(encoded),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--allow-path", action="append", required=True,
                        help="Trusted exact repo-relative path; repeat for each grant")
    args = parser.parse_args(argv)
    try:
        with args.patch.open("rb") as stream:
            data = stream.read(MAX_PATCH_BYTES + 1)
        if len(data) > MAX_PATCH_BYTES:
            raise PatchGuardError("patch exceeds byte budget")
        report = validate_patch(data.decode("utf-8"), args.allow_path)
    except (OSError, UnicodeError, PatchGuardError) as exc:
        print(json.dumps({"accepted": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"accepted": True, **report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
