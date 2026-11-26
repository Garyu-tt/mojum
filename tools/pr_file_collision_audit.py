#!/usr/bin/env python3
"""Offline, read-only cross-PR changed-path collision audit.

Input is a *trusted, frozen snapshot*, not a live GitHub query. A clean result
is advisory and does not replace review, CI, or exact HEAD+BASE merge gates.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys

SCHEMA = "mojum.pr-file-collision-input/1"
REPORT_SCHEMA = "mojum.pr-file-collision-report/1"
MAX_INPUT_BYTES = 256 * 1024
MAX_PRS = 300
MAX_FILES_PER_PR = 32
MAX_TOTAL_PATHS = 8192
MAX_PATH_LENGTH = 180
_PATH_RE = re.compile(r"[A-Za-z0-9_.\-/]+\Z", re.ASCII)
_SHA_RE = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_PR_KEYS = frozenset(
    ("number", "base_sha", "head_sha", "state", "draft",
     "changed_paths", "allowed_paths")
)


class CollisionAuditError(ValueError):
    """Invalid or unsafe offline PR inventory."""


def _fields(value: object, expected: frozenset[str]) -> None:
    if type(value) is not dict:
        raise CollisionAuditError("record must be a JSON object")
    if any(type(key) is not str for key in value):
        raise CollisionAuditError("record keys must be plain strings")
    if set(value) != expected:
        raise CollisionAuditError("record fields mismatch")


def _path(value: object) -> str:
    if type(value) is not str or not (1 <= len(value) <= MAX_PATH_LENGTH):
        raise CollisionAuditError("invalid repository path length or type")
    if not _PATH_RE.fullmatch(value) or value.startswith("/"):
        raise CollisionAuditError("unsafe repository path")
    parts = value.split("/")
    if any(part in ("", ".", "..") or part == ".git" for part in parts):
        raise CollisionAuditError("unsafe repository path segment")
    return value


def _path_list(value: object) -> list[str]:
    if type(value) is not list or not (1 <= len(value) <= MAX_FILES_PER_PR):
        raise CollisionAuditError("path list is missing or exceeds budget")
    checked = [_path(path) for path in value]
    if len(set(checked)) != len(checked):
        raise CollisionAuditError("duplicate repository path")
    return sorted(checked)


def _sha(value: object) -> str:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None:
        raise CollisionAuditError("full lowercase 40-character commit SHA required")
    return value


def _record(value: object) -> dict:
    _fields(value, _PR_KEYS)
    number = value["number"]
    if type(number) is not int or not (1 <= number <= 10_000_000):
        raise CollisionAuditError("invalid PR number")
    if type(value["state"]) is not str or value["state"] != "open":
        raise CollisionAuditError("only frozen OPEN PRs may be audited")
    if type(value["draft"]) is not bool:
        raise CollisionAuditError("draft must be a boolean")
    changed = _path_list(value["changed_paths"])
    allowed = _path_list(value["allowed_paths"])
    return {
        "number": number,
        "base_sha": _sha(value["base_sha"]),
        "head_sha": _sha(value["head_sha"]),
        "state": "open",
        "draft": value["draft"],
        "changed_paths": changed,
        "allowed_paths": allowed,
    }


def audit_manifest(manifest: object) -> dict:
    """Return deterministic findings; never fetch, mutate or trust GitHub state."""
    _fields(manifest, frozenset(("schema", "prs")))
    if type(manifest["schema"]) is not str or manifest["schema"] != SCHEMA:
        raise CollisionAuditError("unsupported manifest schema")
    prs = manifest["prs"]
    if type(prs) is not list or not (1 <= len(prs) <= MAX_PRS):
        raise CollisionAuditError("PR count out of bounds")
    checked = [_record(pr) for pr in prs]
    numbers = [pr["number"] for pr in checked]
    if len(set(numbers)) != len(numbers):
        raise CollisionAuditError("duplicate PR number")
    checked.sort(key=lambda item: item["number"])
    path_total = sum(len(pr["changed_paths"]) for pr in checked)
    if path_total > MAX_TOTAL_PATHS:
        raise CollisionAuditError("changed path budget exceeded")

    owners: dict[str, list[int]] = {}
    out_of_scope: list[dict] = []
    for pr in checked:
        grant = set(pr["allowed_paths"])
        ungranted = sorted(set(pr["changed_paths"]) - grant)
        if ungranted:
            out_of_scope.append({"pr": pr["number"], "paths": ungranted})
        for path in pr["changed_paths"]:
            owners.setdefault(path, []).append(pr["number"])

    # The same filename changed by independent PRs is a review risk.
    same_path = [
        {"path": path, "prs": sorted(ids)}
        for path, ids in sorted(owners.items())
        if len(ids) > 1
    ]
    # A path becoming a file in one PR and a directory in another is also
    # conflicting even if the complete filenames are not identical.
    file_directory = []
    for path in sorted(owners):
        pieces = path.split("/")
        for end in range(1, len(pieces)):
            ancestor = "/".join(pieces[:end])
            if ancestor not in owners:
                continue
            parent_ids = set(owners[ancestor])
            child_ids = set(owners[path])
            if parent_ids != child_ids or len(parent_ids) > 1:
                file_directory.append({
                    "file_path": ancestor,
                    "descendant_path": path,
                    "prs": sorted(parent_ids | child_ids),
                })
            else:
                # One PR contains a file and a nested child: impossible tree.
                file_directory.append({
                    "file_path": ancestor,
                    "descendant_path": path,
                    "prs": sorted(parent_ids),
                })

    canonical = {"schema": SCHEMA, "prs": checked}
    canonical_wire = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return {
        "schema": REPORT_SCHEMA,
        "snapshot_sha256": hashlib.sha256(canonical_wire).hexdigest(),
        "checked_prs": len(checked),
        "checked_changed_paths": path_total,
        "same_path_collisions": same_path,
        "file_directory_collisions": file_directory,
        "out_of_scope": out_of_scope,
        "clear": not (same_path or file_directory or out_of_scope),
    }


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    if len(pairs) > MAX_FILES_PER_PR + 8:
        raise CollisionAuditError("JSON object has too many fields")
    result = {}
    for key, value in pairs:
        if key in result:
            raise CollisionAuditError("duplicate JSON object field")
        result[key] = value
    return result


def _parse_integer(raw: str) -> int:
    if len(raw) > 12:
        raise CollisionAuditError("oversized JSON integer")
    return int(raw)


def _reject_non_integer(_raw: str) -> None:
    raise CollisionAuditError("floating point and nonfinite JSON numbers forbidden")


def audit_json(raw: bytes) -> dict:
    """Accept bounded strict JSON bytes; reject duplicate keys and floats."""
    if type(raw) is not bytes or not (1 <= len(raw) <= MAX_INPUT_BYTES):
        raise CollisionAuditError("input byte budget exceeded or empty input")
    try:
        text = raw.decode("utf-8", "strict")
        document = json.loads(
            text,
            object_pairs_hook=_unique_keys,
            parse_int=_parse_integer,
            parse_float=_reject_non_integer,
            parse_constant=_reject_non_integer,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CollisionAuditError("invalid or over-nested JSON") from exc
    return audit_manifest(document)


def main() -> int:
    """Read one snapshot from standard input; emit one canonical report."""
    try:
        payload = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        report = audit_json(payload)
    except CollisionAuditError as exc:
        print("collision audit input error: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["clear"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
