"""Strict, bounded admission for public GitHub pull-request snapshots.

This module is deliberately offline: callers supply an already-fetched mapping.
The digest is a deterministic consistency witness, not a GitHub signature.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Mapping

SCHEMA = "mojum.qa-pr-snapshot/1"
MAX_DEPENDENCIES = 64
MAX_TEXT = 200
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")
_ALLOWED = frozenset({
    "repository", "number", "state", "draft", "merged", "mergeable",
    "base_ref", "base_sha", "head_ref", "head_sha", "created_at",
    "updated_at", "dependencies",
})
_FORBIDDEN = frozenset({
    "body", "title", "patch", "diff", "token", "authorization",
    "secret", "private_payload", "journal", "html_url", "url",
})


@dataclass(frozen=True)
class PRSnapshot:
    schema: str
    repository: str
    number: int
    state: str
    draft: bool
    merged: bool
    mergeable: bool | None
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    created_at: str
    updated_at: str
    dependencies: tuple[int, ...]
    digest: str


def _text(value: Any, field: str, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or not value or len(value) > MAX_TEXT:
        raise ValueError(f"{field} must be a non-empty bounded string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ValueError(f"{field} has invalid syntax")
    return value


def _repository(value: Any) -> str:
    repository = _text(value, "repository", _REPO_RE)
    owner, name = repository.split("/")
    if owner in {".", ".."} or name in {".", ".."} or owner.startswith(".") or name.startswith("."):
        raise ValueError("repository components are invalid")
    return repository.lower()


def _branch(value: Any, field: str) -> str:
    branch = _text(value, field, _BRANCH_RE)
    parts = branch.split("/")
    if (
        branch.startswith(("/", "-"))
        or branch.endswith(("/", "."))
        or "//" in branch
        or ".." in branch
        or "@{" in branch
        or any(part in {"", ".", ".."} or part.startswith(".") or part.endswith(".lock") for part in parts)
    ):
        raise ValueError(f"{field} is not a valid branch ref")
    return branch


def _timestamp(value: Any, field: str) -> str:
    value = _text(value, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return value


def admit_pr_snapshot(raw: Mapping[str, Any]) -> PRSnapshot:
    """Validate and detach one bounded public PR metadata record."""
    # Only exact built-in dictionaries are admitted. In particular, do not
    # invoke caller-defined Mapping methods while validating untrusted input.
    if type(raw) is not dict:
        raise ValueError("snapshot must be an exact built-in dict")
    if len(raw) > len(_ALLOWED):
        raise ValueError("snapshot has too many fields")

    # Inspect key types before constructing a set: hashing a str subclass can
    # execute caller code. Iterating dict.keys() does not hash the keys again.
    raw_keys = tuple(raw.keys())
    if any(type(key) is not str or len(key) > 32 for key in raw_keys):
        raise ValueError("snapshot keys must be bounded exact native strings")
    keys = set(raw_keys)
    forbidden = keys & _FORBIDDEN
    if forbidden:
        raise ValueError("snapshot contains disallowed payload fields")
    unknown = keys - _ALLOWED
    if unknown:
        raise ValueError("snapshot contains unknown fields")
    missing = _ALLOWED - keys
    if missing:
        raise ValueError("snapshot is missing required fields")

    repository = _repository(raw["repository"])
    number = raw["number"]
    if type(number) is not int or not 1 <= number <= 2**31 - 1:
        raise ValueError("number must be a positive bounded integer")
    state = raw["state"]
    if type(state) is not str or state not in {"open", "closed"}:
        raise ValueError("state must be open or closed")
    for field in ("draft", "merged"):
        if type(raw[field]) is not bool:
            raise ValueError(f"{field} must be a boolean")
    mergeable = raw["mergeable"]
    if mergeable is not None and type(mergeable) is not bool:
        raise ValueError("mergeable must be boolean or null")
    if raw["merged"] and state != "closed":
        raise ValueError("merged snapshots must be closed")
    if raw["draft"] and (state != "open" or raw["merged"]):
        raise ValueError("draft snapshots must be open and not merged")
    base_ref = _branch(raw["base_ref"], "base_ref")
    head_ref = _branch(raw["head_ref"], "head_ref")
    base_sha = _text(raw["base_sha"], "base_sha", _SHA_RE)
    head_sha = _text(raw["head_sha"], "head_sha", _SHA_RE)
    created_at = _timestamp(raw["created_at"], "created_at")
    updated_at = _timestamp(raw["updated_at"], "updated_at")
    if datetime.fromisoformat(updated_at.replace("Z", "+00:00")) < datetime.fromisoformat(created_at.replace("Z", "+00:00")):
        raise ValueError("updated_at precedes created_at")

    deps = raw["dependencies"]
    if type(deps) is not list or len(deps) > MAX_DEPENDENCIES:
        raise ValueError("dependencies must be a bounded list")
    if any(type(dep) is not int or not 1 <= dep <= 2**31 - 1 for dep in deps):
        raise ValueError("dependency IDs must be positive integers")
    if len(set(deps)) != len(deps) or number in deps:
        raise ValueError("dependency IDs must be unique and must not include this PR")

    canonical = {
        "schema": SCHEMA, "repository": repository, "number": number,
        "state": state, "draft": raw["draft"], "merged": raw["merged"],
        "mergeable": mergeable, "base_ref": base_ref, "base_sha": base_sha,
        "head_ref": head_ref, "head_sha": head_sha,
        "created_at": created_at, "updated_at": updated_at,
        "dependencies": sorted(deps),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    digest = hashlib.sha256(encoded).hexdigest()
    return PRSnapshot(
        schema=SCHEMA, repository=repository, number=number, state=state,
        draft=raw["draft"], merged=raw["merged"], mergeable=mergeable,
        base_ref=base_ref, base_sha=base_sha, head_ref=head_ref, head_sha=head_sha,
        created_at=created_at, updated_at=updated_at,
        dependencies=tuple(sorted(deps)), digest=digest,
    )
