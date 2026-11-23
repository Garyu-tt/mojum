#!/usr/bin/env python3
"""Offline, read-only audit of explicitly declared Mojum PR stack dependencies.

No GitHub access, credentials, filesystem mutation, or implicit PR acceptance.
"""

from __future__ import annotations

import argparse
import heapq
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = "mojum.pr-stack-snapshot/1"
REPORT_SCHEMA = "mojum.pr-stack-audit/1"
MAX_INPUT_BYTES = 1_048_576
MAX_PRS = 512
MAX_DEPENDENCIES_PER_PR = 64
MAX_TOTAL_DEPENDENCIES = 4096
MAX_PR_NUMBER = 1_000_000_000
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_SNAPSHOT_KEYS = frozenset({"schema", "base_branch", "pull_requests"})
_PR_KEYS = frozenset({
    "number", "state", "head_ref", "head_sha", "base_ref",
    "depends_on", "base_pr", "parent_head_sha",
})
_STATES = frozenset({"open", "closed", "merged"})


class SnapshotError(ValueError):
    """Untrusted snapshot cannot be admitted for analysis."""


def _failure(message: str) -> None:
    raise SnapshotError(message)


def _exact_keys(value: Any, required: frozenset[str], where: str) -> None:
    if type(value) is not dict or value.keys() != required:
        _failure(f"{where}: expected exact object keys")


def _branch(value: Any, where: str) -> str:
    if type(value) is not str or _REF.fullmatch(value) is None:
        _failure(f"{where}: invalid branch ref")
    if ".." in value or value.endswith(".") or "//" in value or value.endswith("/"):
        _failure(f"{where}: noncanonical branch ref")
    return value


def _sha(value: Any, where: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _failure(f"{where}: expected lowercase 40-digit Git SHA")
    return value


def _pr_number(value: Any, where: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_PR_NUMBER:
        _failure(f"{where}: invalid PR number")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            _failure("duplicate JSON object field")
        obj[key] = value
    return obj


def _parse_int(text: str) -> int:
    if len(text) > 10:
        _failure("oversized JSON integer")
    return int(text)


def decode_snapshot(data: bytes) -> dict[str, Any]:
    if type(data) is not bytes or not data or len(data) > MAX_INPUT_BYTES:
        _failure("snapshot size outside bounds")
    try:
        snapshot = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_int=_parse_int,
            parse_constant=lambda _: _failure("non-finite JSON numeric literal"),
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, SnapshotError):
            raise
        _failure("invalid JSON snapshot")
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot: Any) -> None:
    _exact_keys(snapshot, _SNAPSHOT_KEYS, "snapshot")
    if type(snapshot["schema"]) is not str or snapshot["schema"] != SCHEMA:
        _failure("unsupported snapshot schema")
    _branch(snapshot["base_branch"], "base_branch")
    prs = snapshot["pull_requests"]
    if type(prs) is not list or len(prs) > MAX_PRS:
        _failure("pull_requests exceeds list budget")
    seen: set[int] = set()
    total_edges = 0
    for i, pr in enumerate(prs):
        label = f"pull_requests[{i}]"
        _exact_keys(pr, _PR_KEYS, label)
        n = _pr_number(pr["number"], f"{label}.number")
        if n in seen:
            _failure("duplicate PR number")
        seen.add(n)
        if type(pr["state"]) is not str or pr["state"] not in _STATES:
            _failure(f"{label}.state: invalid state")
        _branch(pr["head_ref"], f"{label}.head_ref")
        _branch(pr["base_ref"], f"{label}.base_ref")
        _sha(pr["head_sha"], f"{label}.head_sha")
        deps = pr["depends_on"]
        if type(deps) is not list or len(deps) > MAX_DEPENDENCIES_PER_PR:
            _failure(f"{label}.depends_on: exceeds list budget")
        total_edges += len(deps)
        if total_edges > MAX_TOTAL_DEPENDENCIES:
            _failure("total dependency budget exceeded")
        dep_ids: set[int] = set()
        for dep in deps:
            _pr_number(dep, f"{label}.depends_on")
            if dep in dep_ids:
                _failure(f"{label}.depends_on: duplicate dependency")
            dep_ids.add(dep)
        if pr["base_pr"] is not None:
            _pr_number(pr["base_pr"], f"{label}.base_pr")
        if pr["parent_head_sha"] is not None:
            _sha(pr["parent_head_sha"], f"{label}.parent_head_sha")
        if (pr["base_pr"] is None) != (pr["parent_head_sha"] is None):
            _failure(f"{label}: base_pr and parent_head_sha must be jointly set")


def _cycles(prs: dict[int, dict[str, Any]]) -> list[list[int]]:
    """Return canonical, deterministic cycles; at most one witness per DFS back edge."""
    state: dict[int, int] = {}
    stack: list[int] = []
    index: dict[int, int] = {}
    seen_cycles: set[tuple[int, ...]] = set()

    def dfs(number: int) -> None:
        state[number] = 1
        index[number] = len(stack)
        stack.append(number)
        for dep in sorted(prs[number]["depends_on"]):
            if dep not in prs:
                continue
            if state.get(dep, 0) == 0:
                dfs(dep)
            elif state[dep] == 1:
                loop = stack[index[dep]:]
                start = loop.index(min(loop))
                ordered = tuple(loop[start:] + loop[:start])
                seen_cycles.add(ordered)
        stack.pop()
        del index[number]
        state[number] = 2

    # MAX_PRS=512, below typical 1000-frame recursion limits.
    for number in sorted(prs):
        if state.get(number, 0) == 0:
            dfs(number)
    return [list(cycle) + [cycle[0]] for cycle in sorted(seen_cycles)]


def _topological_order(prs: dict[int, dict[str, Any]]) -> list[int]:
    remaining = {n: sum(dep in prs for dep in pr["depends_on"]) for n, pr in prs.items()}
    dependents: dict[int, list[int]] = {n: [] for n in prs}
    for n, pr in prs.items():
        for dep in pr["depends_on"]:
            if dep in prs:
                dependents[dep].append(n)
    ready = [n for n, count in remaining.items() if count == 0]
    heapq.heapify(ready)
    result: list[int] = []
    while ready:
        n = heapq.heappop(ready)
        result.append(n)
        for child in sorted(dependents[n]):
            remaining[child] -= 1
            if remaining[child] == 0:
                heapq.heappush(ready, child)
    return result


def audit(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Audit a fully validated snapshot, with deterministic machine-readable findings."""
    validate_snapshot(snapshot)
    prs = {pr["number"]: pr for pr in snapshot["pull_requests"]}
    issues: list[dict[str, Any]] = []

    def finding(code: str, pr: int, related_pr: int | None = None) -> None:
        row: dict[str, Any] = {"code": code, "pr": pr}
        if related_pr is not None:
            row["related_pr"] = related_pr
        issues.append(row)

    refs: dict[str, int] = {}
    for number in sorted(prs):
        pr = prs[number]
        if pr["head_ref"] in refs:
            finding("duplicate_head_ref", number, refs[pr["head_ref"]])
        else:
            refs[pr["head_ref"]] = number
        for dep in sorted(pr["depends_on"]):
            if dep == number:
                finding("self_dependency", number)
            elif dep not in prs:
                finding("missing_dependency", number, dep)
            elif prs[dep]["state"] == "closed":
                finding("closed_dependency", number, dep)
        base_pr = pr["base_pr"]
        if base_pr is None:
            if pr["base_ref"] != snapshot["base_branch"]:
                finding("untracked_base_ref", number)
        else:
            if base_pr not in pr["depends_on"]:
                finding("base_parent_not_declared", number, base_pr)
            if base_pr not in prs:
                finding("missing_base_parent", number, base_pr)
            else:
                parent = prs[base_pr]
                if parent["state"] != "open":
                    finding("inactive_base_parent", number, base_pr)
                if pr["base_ref"] != parent["head_ref"]:
                    finding("base_ref_mismatch", number, base_pr)
                if pr["parent_head_sha"] != parent["head_sha"]:
                    finding("stale_parent_head", number, base_pr)
    cycles = _cycles(prs)
    for cycle in cycles:
        issues.append({"code": "dependency_cycle", "cycle": cycle})
    issues.sort(key=lambda row: (
        row["code"], row.get("pr", 0), row.get("related_pr", 0), tuple(row.get("cycle", []))
    ))
    order = _topological_order(prs)
    return {
        "schema": REPORT_SCHEMA,
        "ok": not issues,
        "pr_count": len(prs),
        "dependency_count": sum(len(pr["depends_on"]) for pr in prs.values()),
        "topological_order": order if len(order) == len(prs) and not issues else [],
        "blockers": issues,
    }


def read_snapshot(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise SnapshotError("cannot read snapshot file") from exc
    return decode_snapshot(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="local, explicitly supplied normalized PR JSON")
    args = parser.parse_args(argv)
    try:
        report = audit(read_snapshot(args.snapshot))
    except SnapshotError as exc:
        print(json.dumps({"schema": REPORT_SCHEMA, "ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
