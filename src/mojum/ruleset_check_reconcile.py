"""Fail-closed reconciliation of branch ruleset requirements and GitHub Check Runs.

This module consumes bounded API snapshots. It does not fetch GitHub data,
approve reviews, merge pull requests, or change repository protection.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

SCHEMA = "mojum.ruleset-check-reconcile/1"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_MAX_REQUIRED = 32
_MAX_RUNS = 256
_MAX_CONTEXT = 200
_PASSING = frozenset({"success", "neutral"})
_FAILING = frozenset({
    "failure", "cancelled", "timed_out", "action_required", "startup_failure",
    "stale", "skipped",
})


@dataclass(frozen=True)
class Reconciliation:
    schema: str
    verdict: str
    expected_sha: str
    required: tuple[str, ...]
    passed: tuple[str, ...]
    missing: tuple[str, ...]
    pending: tuple[str, ...]
    failed: tuple[str, ...]
    ambiguous: tuple[str, ...]
    reasons: tuple[str, ...]
    receipt_sha256: str


def _dict(value: Any, field: str) -> dict:
    if type(value) is not dict:
        raise ValueError(f"{field} must be a native dict")
    return value


def _list(value: Any, field: str, maximum: int) -> list:
    if type(value) is not list or len(value) > maximum:
        raise ValueError(f"{field} must be a native list of at most {maximum} entries")
    return value


def _token(value: Any, field: str, maximum: int = _MAX_CONTEXT) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError(f"{field} must be a non-empty native string of at most {maximum} characters")
    if value != value.strip() or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{field} contains whitespace or control characters outside the token contract")
    return value


def _positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0 or value > 2_147_483_647:
        raise ValueError(f"{field} must be a positive bounded native integer")
    return value


def _sha(value: Any, field: str) -> str:
    if type(value) is not str or not _SHA.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase 40-character commit SHA")
    return value


def _receipt(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def reconcile_ruleset_checks(ruleset: dict, check_runs_snapshot: dict, expected_sha: str) -> Reconciliation:
    """Compare active main ruleset descriptors with complete Check Runs for one exact SHA.

    GitHub's required check identity is the pair (context, integration_id).
    A successful legacy combined-status entry is deliberately not accepted as
    a substitute for a Check Run from the required GitHub App.
    """
    expected = _sha(expected_sha, "expected_sha")
    ruleset = _dict(ruleset, "ruleset")
    snapshot = _dict(check_runs_snapshot, "check_runs_snapshot")

    enforcement = _token(ruleset.get("enforcement"), "ruleset.enforcement", 32)
    conditions = _dict(ruleset.get("conditions"), "ruleset.conditions")
    ref_name = _dict(conditions.get("ref_name"), "ruleset.conditions.ref_name")
    includes = _list(ref_name.get("include"), "ruleset ref_name.include", 32)
    excludes = _list(ref_name.get("exclude"), "ruleset ref_name.exclude", 32)
    if any(type(x) is not str for x in includes + excludes):
        raise ValueError("ruleset ref_name patterns must be native strings")
    main_ref = "refs/heads/main"

    rules = _list(ruleset.get("rules"), "ruleset.rules", 64)
    required_rules = []
    for rule in rules:
        item = _dict(rule, "ruleset rule")
        if item.get("type") == "required_status_checks":
            required_rules.append(item)

    if len(required_rules) != 1:
        raise ValueError("ruleset must contain exactly one required_status_checks rule")
    parameters = _dict(required_rules[0].get("parameters"), "required_status_checks.parameters")
    strict = parameters.get("strict_required_status_checks_policy")
    if type(strict) is not bool:
        raise ValueError("strict_required_status_checks_policy must be a native bool")
    descriptors = _list(parameters.get("required_status_checks"), "required_status_checks", _MAX_REQUIRED)
    if not descriptors:
        raise ValueError("ruleset must declare at least one required status check")

    required_pairs = []
    contexts = set()
    for index, raw in enumerate(descriptors):
        descriptor = _dict(raw, f"required_status_checks[{index}]")
        context = _token(descriptor.get("context"), "required check context")
        app_id = _positive_int(descriptor.get("integration_id"), "required check integration_id")
        if context in contexts:
            raise ValueError("ruleset contains duplicate required check contexts")
        contexts.add(context)
        required_pairs.append((context, app_id))

    total_count = snapshot.get("total_count")
    if type(total_count) is not int or total_count < 0 or total_count > _MAX_RUNS:
        raise ValueError(f"check_runs.total_count must be a native integer in 0..{_MAX_RUNS}")
    runs = _list(snapshot.get("check_runs"), "check_runs.check_runs", _MAX_RUNS)
    if total_count != len(runs):
        # A partial page must never be interpreted as a missing or successful check.
        complete = False
    else:
        complete = True

    by_identity: dict[tuple[str, int], list[dict]] = {}
    for index, raw in enumerate(runs):
        run = _dict(raw, f"check_runs[{index}]")
        name = _token(run.get("name"), "check run name")
        head = _sha(run.get("head_sha"), "check run head_sha")
        status = _token(run.get("status"), "check run status", 32).lower()
        conclusion = run.get("conclusion")
        if conclusion is not None:
            conclusion = _token(conclusion, "check run conclusion", 32).lower()
        app = run.get("app")
        app_id = None
        if app is not None:
            app = _dict(app, "check run app")
            if "id" in app and app["id"] is not None:
                app_id = _positive_int(app["id"], "check run app.id")
        if head != expected or app_id is None:
            continue
        by_identity.setdefault((name, app_id), []).append({
            "status": status,
            "conclusion": conclusion,
        })

    passed: list[str] = []
    missing: list[str] = []
    pending: list[str] = []
    failed: list[str] = []
    ambiguous: list[str] = []
    reasons: list[str] = []

    if enforcement != "active":
        reasons.append("ruleset_not_active")
    if main_ref not in includes or main_ref in excludes:
        reasons.append("ruleset_does_not_target_main")
    if not strict:
        reasons.append("strict_required_checks_disabled")
    if not complete:
        reasons.append("check_runs_page_incomplete")

    for context, app_id in required_pairs:
        matches = by_identity.get((context, app_id), [])
        if not matches:
            missing.append(context)
            continue
        if len(matches) != 1:
            ambiguous.append(context)
            continue
        run = matches[0]
        if run["status"] != "completed":
            pending.append(context)
            continue
        conclusion = run["conclusion"]
        if conclusion in _PASSING:
            passed.append(context)
        elif conclusion in _FAILING:
            failed.append(context)
        else:
            pending.append(context)

    if reasons or missing or pending or ambiguous:
        verdict = "HOLD"
    elif failed:
        verdict = "REJECT"
    else:
        verdict = "PASS"

    payload = {
        "schema": SCHEMA,
        "verdict": verdict,
        "expected_sha": expected,
        "required": [context for context, _ in required_pairs],
        "passed": sorted(passed),
        "missing": sorted(missing),
        "pending": sorted(pending),
        "failed": sorted(failed),
        "ambiguous": sorted(ambiguous),
        "reasons": sorted(reasons),
    }
    return Reconciliation(
        schema=SCHEMA,
        verdict=verdict,
        expected_sha=expected,
        required=tuple(context for context, _ in required_pairs),
        passed=tuple(sorted(passed)),
        missing=tuple(sorted(missing)),
        pending=tuple(sorted(pending)),
        failed=tuple(sorted(failed)),
        ambiguous=tuple(sorted(ambiguous)),
        reasons=tuple(sorted(reasons)),
        receipt_sha256=_receipt(payload),
    )
