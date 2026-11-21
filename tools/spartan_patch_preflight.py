"""Conservative, read-only gate for agent-generated Git unified diff text.

This is a lexical preflight, NOT a sandbox, parser for arbitrary Git patches,
or replacement for applying patches inside a credential-free worktree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys

MAX_BYTES = 256 * 1024
MAX_FILES = 16
MAX_HUNKS_PER_FILE = 32
MAX_CHANGED_LINES = 400
_PATH = re.compile(r"[A-Za-z0-9_./-]+\Z")
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: [^\r\n]*)?\Z")
_INDEX = re.compile(r"index [0-9a-f]{7,64}\.\.[0-9a-f]{7,64}(?: 100644)?\Z")


class PatchRejected(ValueError):
    """Untrusted patch did not meet the deliberately narrow contract."""


def _path(raw: str, allow: tuple[str, ...]) -> str:
    if not raw or len(raw) > 240 or not _PATH.fullmatch(raw):
        raise PatchRejected("unsafe or unsupported patch path")
    bits = raw.split("/")
    if any(not b or b in (".", "..") or b.startswith(".") for b in bits):
        raise PatchRejected("dot or empty path component")
    if not any(raw.startswith(prefix) and len(raw) > len(prefix) for prefix in allow):
        raise PatchRejected("path outside explicit allowlist")
    return raw


def _checkout_path(root: Path, name: str, creation: bool) -> None:
    """Best-effort link/mode preflight; caller MUST still apply in isolation."""
    here = root
    parts = name.split("/")
    for part in parts[:-1]:
        here = here / part
        try:
            mode = here.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(mode):
            raise PatchRejected("checkout parent is not a real directory")
    target = here / parts[-1]
    try:
        mode = target.lstat().st_mode
    except FileNotFoundError:
        if not creation:
            raise PatchRejected("modified checkout file does not exist")
    else:
        if creation or not stat.S_ISREG(mode):
            raise PatchRejected("checkout target exists or is not a regular file")


def inspect_patch(blob: bytes, *, allow: tuple[str, ...], checkout: Path | None = None) -> dict:
    """Fail closed on ambiguous modes, paths, hunks and oversized diffs."""
    if not blob or len(blob) > MAX_BYTES:
        raise PatchRejected("empty or oversized patch")
    if b"\x00" in blob or b"\r" in blob:
        raise PatchRejected("NUL or CR in patch")
    try:
        source = blob.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise PatchRejected("patch is not valid UTF-8") from exc
    if not source.endswith("\n"):
        raise PatchRejected("patch must end with newline")
    if not allow or any(not x.endswith("/") or _path(x[:-1] + "/file", (x,)) != x[:-1] + "/file" for x in allow):
        raise PatchRejected("invalid or missing allowlist")
    if checkout is not None:
        if not checkout.is_dir() or checkout.is_symlink():
            raise PatchRejected("checkout must be a real directory")
    lines = source.splitlines()
    i = 0
    files: list[str] = []
    changes = 0
    while i < len(lines):
        header = lines[i]
        if not header.startswith("diff --git a/"):
            raise PatchRejected("expected unquoted diff --git header")
        pair = header[len("diff --git a/"):].split(" b/")
        if len(pair) != 2 or pair[0] != pair[1]:
            raise PatchRejected("rename, copy, or malformed diff header")
        name = _path(pair[0], allow)
        if name in files or len(files) >= MAX_FILES:
            raise PatchRejected("duplicate file or file count exceeded")
        files.append(name)
        i += 1
        creation = False
        seen_index = False
        while i < len(lines) and not lines[i].startswith("--- "):
            if lines[i] == "new file mode 100644" and not creation and not seen_index:
                creation = True
            elif _INDEX.fullmatch(lines[i]) and not seen_index:
                seen_index = True
            else:
                raise PatchRejected("unsupported file mode or diff metadata")
            i += 1
        if not seen_index or i + 1 >= len(lines):
            raise PatchRejected("missing index or file path headers")
        if lines[i] != ("--- /dev/null" if creation else "--- a/" + name):
            raise PatchRejected("invalid old file path")
        if lines[i + 1] != "+++ b/" + name:
            raise PatchRejected("invalid new file path")
        if checkout is not None:
            _checkout_path(checkout, name, creation)
        i += 2
        hunks = 0
        last_old_end = 0
        last_new_end = 0
        while i < len(lines) and lines[i].startswith("@@ "):
            match = _HUNK.fullmatch(lines[i])
            if match is None:
                raise PatchRejected("invalid hunk header")
            old_start, old_count, new_start, new_count = (
                int(match[1]), int(match[2] or "1"),
                int(match[3]), int(match[4] or "1"),
            )
            if not old_count and not new_count:
                raise PatchRejected("empty hunk")
            if creation and (old_start != 0 or old_count != 0):
                raise PatchRejected("creation hunk refers to old file")
            if old_start < last_old_end or new_start < last_new_end:
                raise PatchRejected("overlapping or reversed hunks")
            last_old_end = old_start + old_count
            last_new_end = new_start + new_count
            hunks += 1
            if hunks > MAX_HUNKS_PER_FILE:
                raise PatchRejected("too many hunks")
            i += 1
            remaining_old = old_count
            remaining_new = new_count
            while remaining_old or remaining_new:
                if i >= len(lines):
                    raise PatchRejected("truncated hunk")
                line = lines[i]
                if line.startswith(" "):
                    remaining_old -= 1
                    remaining_new -= 1
                elif line.startswith("+"):
                    remaining_new -= 1
                    changes += 1
                elif line.startswith("-"):
                    remaining_old -= 1
                    changes += 1
                else:
                    raise PatchRejected("unrecognized line inside hunk")
                if min(remaining_old, remaining_new) < 0 or changes > MAX_CHANGED_LINES:
                    raise PatchRejected("hunk line count or change budget exceeded")
                i += 1
        if not hunks:
            raise PatchRejected("file has no content hunks")
        if i < len(lines) and not lines[i].startswith("diff --git "):
            raise PatchRejected("unexpected content between files")
    return {"status": "candidate", "sha256": hashlib.sha256(blob).hexdigest(),
            "files": files, "changed_lines": changes,
            "warning": "lexical preflight only; apply and test in a credential-free sandbox"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow", action="append", required=True, metavar="DIRECTORY/",
                        help="explicit permitted relative directory prefix (repeatable)")
    parser.add_argument("--checkout", type=Path, help="optional existing worktree for link/mode checks")
    args = parser.parse_args(argv)
    try:
        report = inspect_patch(sys.stdin.buffer.read(MAX_BYTES + 1),
                               allow=tuple(args.allow), checkout=args.checkout)
    except PatchRejected as exc:
        print(json.dumps({"status": "reject", "reason": str(exc)}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
