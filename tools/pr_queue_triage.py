"""Read-only, offline PR queue triage. This NEVER decides or performs a merge.

Triage deliberately stops short of the exact-head/base and independent review
required by docs/MERGE_ACCEPTANCE_POLICY.md. Candidate != mergeable/approved.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Mapping

SCHEMA = "mojum.pr-queue-triage/1"
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_PRS = 500
MAX_CHECKS = 512
MAX_REQUIREMENTS = 64
_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
_FAILURES = frozenset({
    "FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED",
    "STALE", "STARTUP_FAILURE", "SKIPPED", "NEUTRAL",
})


class TriageInputError(ValueError):
    """Invalid input, which must never become positive CI evidence."""


def _unique_json_object(pairs):
    """Refuse duplicate keys at every depth of an untrusted JSON snapshot."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise TriageInputError("duplicate key in snapshot JSON")
        result[key] = value
    return result


def _reject_nonfinite_json(value):
    raise TriageInputError("non-finite JSON value in snapshot")


def _sha(value):
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _required(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= MAX_REQUIREMENTS:
        raise TriageInputError("1..64 explicit required CI job labels are mandatory")
    if any(not isinstance(v, str) or not v.strip() or v != v.strip()
           or any(c in v for c in "*?[]") for v in values):
        raise TriageInputError("requirements must be exact nonempty job labels, not globs")
    if len(set(values)) != len(values):
        raise TriageInputError("duplicate requirements are forbidden")
    return tuple(values)


def _normalized_check(raw, index):
    if not isinstance(raw, dict):
        raise TriageInputError(f"check[{index}] must be an object")
    name = raw.get("name") or raw.get("context")
    if not isinstance(name, str) or not name.strip():
        raise TriageInputError(f"check[{index}] has no name/context")
    workflow = raw.get("workflowName", raw.get("workflow"))
    if workflow is not None and (not isinstance(workflow, str) or not workflow.strip()):
        raise TriageInputError(f"check[{index}] has an invalid workflow name")
    label = f"{workflow.strip()} / {name.strip()}" if workflow else name.strip()
    status = raw.get("status")
    conclusion = raw.get("conclusion")
    if status is None and raw.get("state") is not None:
        state = raw["state"]
        if not isinstance(state, str):
            raise TriageInputError(f"check[{index}] has an invalid state")
        state = state.upper()
        if state in ("SUCCESS", "FAILURE", "ERROR"):
            status, conclusion = "COMPLETED", state
        else:
            status = state
    if status is None and conclusion is not None:
        status = "COMPLETED"
    if not isinstance(status, str):
        raise TriageInputError(f"check[{index}] has no valid status")
    if conclusion is not None and not isinstance(conclusion, str):
        raise TriageInputError(f"check[{index}] has an invalid conclusion")
    head = raw.get("headSha", raw.get("head_sha"))
    if head is not None and not _sha(head):
        raise TriageInputError(f"check[{index}] has an invalid head SHA")
    return (label, status.upper(), conclusion.upper() if conclusion else None, head)


def inspect_pr(raw, *, base_sha, requirements):
    """Return a deterministic triage record, never a merge authorization."""
    if not isinstance(raw, Mapping):
        return {"number": None, "status": "invalid", "reasons": ["INVALID_PR_OBJECT"]}
    number = raw.get("number")
    if type(number) is not int or number <= 0:
        return {"number": None, "status": "invalid", "reasons": ["INVALID_PR_NUMBER"]}
    result = {"number": number, "status": "defer", "reasons": [],
              "head_sha": raw.get("headRefOid") if _sha(raw.get("headRefOid")) else None,
              "unverified_check_heads": []}
    reasons = result["reasons"]
    if result["head_sha"] is None or not _sha(raw.get("baseRefOid")):
        result.update(status="invalid", reasons=["MISSING_OR_INVALID_PINNED_SHAS"])
        return result
    if type(raw.get("isDraft")) is not bool:
        result.update(status="invalid", reasons=["INVALID_DRAFT_FLAG"])
        return result
    if raw.get("baseRefName") != "main":
        reasons.append("WRONG_BASE_BRANCH")
    if raw["baseRefOid"].lower() != base_sha.lower():
        reasons.append("BASE_SHA_CHANGED")
    if raw["isDraft"]:
        reasons.append("DRAFT")
    if raw.get("mergeable") != "MERGEABLE":
        reasons.append("MERGEABILITY_UNPROVEN")
    if raw.get("mergeStateStatus") != "CLEAN":
        reasons.append("MERGE_STATE_NOT_CLEAN")
    rollup = raw.get("statusCheckRollup", [])
    if rollup is None:
        rollup = []
    if not isinstance(rollup, list) or len(rollup) > MAX_CHECKS:
        result.update(status="invalid", reasons=["INVALID_CHECK_ROLLUP"])
        return result
    try:
        checks = [_normalized_check(c, i) for i, c in enumerate(rollup)]
    except TriageInputError:
        result.update(status="invalid", reasons=["MALFORMED_CHECK_ENTRY"])
        return result
    by_name = {}
    for check in checks:
        by_name.setdefault(check[0], []).append(check)
    hard_failure = False
    for needed in requirements:
        matched = by_name.get(needed, [])
        if not matched:
            reasons.append(f"MISSING_REQUIRED_CHECK: {needed}")
            continue
        if len(matched) != 1:
            reasons.append(f"AMBIGUOUS_CHECK_LABEL: {needed}")
            continue
        _label, status, conclusion, check_head = matched[0]
        # Explicit negative conclusions win over contradictory pending status.
        if conclusion in _FAILURES:
            reasons.append(f"NON_SUCCESS_CHECK: {needed} ({conclusion})")
            hard_failure = True
        elif status != "COMPLETED" or conclusion != "SUCCESS":
            reasons.append(f"INCOMPLETE_CHECK: {needed}")
        # A PR rollup often omits the actual check-run SHA or uses the synthetic
        # merge SHA. This inventory CANNOT prove the tested HEAD+BASE.
        if check_head is None or check_head.lower() != result["head_sha"].lower():
            result["unverified_check_heads"].append(needed)
    if hard_failure:
        result["status"] = "blocked"
    elif not reasons:
        result["status"] = "candidate_for_independent_acceptance"
    return result


def scan_queue(data, required):
    requirements = _required(required)
    if not isinstance(data, dict) or not _sha(data.get("base_sha")):
        raise TriageInputError("input needs an independently pinned full base_sha")
    snapshots = data.get("pull_requests")
    if not isinstance(snapshots, list) or len(snapshots) > MAX_PRS:
        raise TriageInputError("pull_requests must be an array of at most 500 snapshots")
    entries = [inspect_pr(pr, base_sha=data["base_sha"],
                          requirements=requirements) for pr in snapshots]
    counts = Counter(entry["number"] for entry in entries if entry["number"] is not None)
    for entry in entries:
        if entry["number"] is not None and counts[entry["number"]] > 1:
            entry["status"] = "invalid"
            entry["reasons"].append("DUPLICATE_PR_NUMBER")
    entries.sort(key=lambda entry: (entry["number"] is None, entry["number"] or 0))
    totals = Counter(entry["status"] for entry in entries)
    return {
        "schema": SCHEMA,
        "base_sha": data["base_sha"].lower(),
        "required_checks": list(requirements),
        "total": len(entries),
        "counts": dict(sorted(totals.items())),
        "pull_requests": entries,
        "merge_performed": False,
        "merge_authorized": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline read-only PR queue triage; never approves merges")
    parser.add_argument("--input", type=Path, required=True, help="bounded JSON queue snapshot")
    parser.add_argument("--require", action="append", required=True, metavar="EXACT_JOB")
    args = parser.parse_args(argv)
    try:
        with args.input.open("rb") as stream:
            data = stream.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise TriageInputError("snapshot exceeds 4 MiB")
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_nonfinite_json,
        )
        report = scan_queue(payload, args.require)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(json.dumps({"schema": SCHEMA, "error": str(exc),
                          "merge_authorized": False, "merge_performed": False}))
        return 2
    print(json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0  # Report generated, NOT any permission to merge.


if __name__ == "__main__":
    raise SystemExit(main())
