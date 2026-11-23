"""Strict, read-only admission for untrusted relative archive member paths.

This module never opens files or follows symlinks. A caller must still enforce
filesystem containment at open time (e.g. dirfd + O_NOFOLLOW).
"""
from __future__ import annotations

import re
from collections.abc import Iterable

MAX_MEMBERS = 4096
MAX_PATH_BYTES = 1024
MAX_COMPONENT_BYTES = 255
_DRIVE = re.compile(r"^[A-Za-z]:")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class ArchivePathError(ValueError):
    """An archive member path is unsafe or ambiguous."""


def validate_member_path(path: str) -> str:
    """Return a canonical relative POSIX member path or fail closed.

    No normalization of unsafe input is performed: callers must not silently
    reinterpret a malicious member name.
    """
    if type(path) is not str:
        raise ArchivePathError("member path must be an exact native string")
    try:
        encoded_path = path.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ArchivePathError("member path is not valid UTF-8 text") from exc
    if not path or len(encoded_path) > MAX_PATH_BYTES:
        raise ArchivePathError("empty or oversized member path")
    if path.startswith(("/", "\\\\")) or _DRIVE.match(path):
        raise ArchivePathError("absolute or drive-prefixed member path")
    if "\\" in path or _CONTROL.search(path):
        raise ArchivePathError("backslash or control character in member path")
    components = path.split("/")
    if any(
        component in ("", ".", "..")
        or ":" in component
        or len(component.encode("utf-8")) > MAX_COMPONENT_BYTES
        or component.endswith((" ", "."))
        for component in components
    ):
        raise ArchivePathError("unsafe path component")
    return path


def admit_member_paths(paths: Iterable[str], *, max_members: int = MAX_MEMBERS) -> tuple[str, ...]:
    """Validate a bounded manifest, refusing duplicate and prefix collisions.

    Prefix collisions (e.g. 'a' and 'a/b') cannot coexist as files. Casefold
    collisions are rejected for portability across case-insensitive hosts.
    Colons are rejected in every component to exclude Windows alternate data
    stream syntax (for example ``file.txt:payload``).
    """
    if type(max_members) is not int or not 1 <= max_members <= MAX_MEMBERS:
        raise ArchivePathError("invalid member limit")
    if isinstance(paths, (str, bytes)):
        raise ArchivePathError("expected an iterable of member paths")
    result: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if len(result) >= max_members:
            raise ArchivePathError("too many archive members")
        member = validate_member_path(path)
        folded = member.casefold()
        if folded in seen:
            raise ArchivePathError("duplicate or casefold-colliding member")
        seen.add(folded)
        result.append(member)
    for member in result:
        parts = member.casefold().split("/")
        if any("/".join(parts[:n]) in seen for n in range(1, len(parts))):
            raise ArchivePathError("file/directory prefix collision")
    return tuple(result)
