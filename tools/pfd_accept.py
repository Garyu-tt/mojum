"""Read-only legacy PFD dispatcher. Merge mode is disabled pending trusted gate."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
SHA = re.compile(r"[0-9a-f]{40}")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ROOT = Path(__file__).resolve().parents[1]

def gh(*args):
    return json.loads(subprocess.check_output(["gh", *args], text=True, timeout=60))

def validate(config):
    if not isinstance(config, dict) or not REPO.fullmatch(config.get("repository", "")):
        raise ValueError("invalid repository")
    plugins = config["plugins"]
    if not isinstance(plugins, dict):
        raise ValueError("invalid plugins")
    for name, p in plugins.items():
        if not SLUG.fullmatch(name) or not isinstance(p, dict):
            raise ValueError("invalid plugin")
        checks = p["checks"]
        if not isinstance(checks, dict) or not checks:
            raise ValueError("required checks missing")
        if any(not isinstance(k, str) or k.count(" / ") != 1 or
               not k.strip() or any(c in k for c in "*?[]") or
               type(v) is not int or v <= 0 for k, v in checks.items()):
            raise ValueError("checks must map exact workflow / job to trusted app ID")
        if len({k.split(" / ", 1)[1] for k in checks}) != len(checks):
            raise ValueError("ambiguous job name")
    return config

def check_protection(protection, checks):
    status = protection.get("required_status_checks") or {}
    review = protection.get("required_pull_request_reviews") or {}
    expected = {(k.split(" / ", 1)[1], v) for k, v in checks.items()}
    actual = {(c["context"], c.get("app_id")) for c in status.get("checks", [])}
    contexts = set(status.get("contexts", []))
    return (status.get("strict") is True and expected == actual and
            contexts <= {k for k, _ in actual} and
            review.get("required_approving_review_count", 0) >= 1 and
            review.get("dismiss_stale_reviews") is True and
            review.get("require_last_push_approval") is True and
            not any((review.get("bypass_pull_request_allowances") or {}).values()) and
            (protection.get("enforce_admins") or {}).get("enabled") is True and
            not (protection.get("allow_force_pushes") or {}).get("enabled") and
            not (protection.get("allow_deletions") or {}).get("enabled"))

def check_pr(pr, repo, plugin, checks):
    base = "plugin/" + plugin
    sha = pr.get("headRefOid", "")
    if pr.get("baseRefName") != base or not SHA.fullmatch(sha):
        return "invalid-target-or-head"
    if pr.get("isDraft") or pr.get("isCrossRepository"):
        return "draft-or-external"
    if pr.get("reviewDecision") != "APPROVED":
        return "review-required"
    if pr.get("mergeStateStatus") != "CLEAN":
        return "not-clean"
    if not pr.get("headRefName", "").startswith("dev/" + plugin + "/"):
        return "unexpected-source"
    if pr.get("headRepository", {}).get("nameWithOwner") != repo:
        return "unexpected-repository"
    rollup = pr.get("statusCheckRollup") or []
    for label in checks:
        matches = [c for c in rollup if c.get("workflowName", "") +
                   " / " + c.get("name", "") == label]
        if len(matches) != 1 or matches[0].get("status") != "COMPLETED" or matches[0].get("conclusion") != "SUCCESS":
            return "required-check-not-success:" + label
    return "candidate"

def inspect(repo, plugin, checks, pr, apply):
    if apply:
        return "apply-disabled"
    reason = check_pr(pr, repo, plugin, checks)
    if reason != "candidate":
        return reason
    base = "plugin/" + plugin
    protection = gh("api", f"repos/{repo}/branches/{quote(base, safe='')}/protection")
    if not check_protection(protection, checks):
        return "branch-protection-not-verified"
    return "candidate-only-not-authorized"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "pfd" / "acceptance.json")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.apply:
        parser.error("apply disabled: trusted CI and independent review not established")
    if not 1 <= args.limit <= 1000:
        parser.error("limit must be 1..1000")
    cfg = validate(json.loads(args.config.read_text(encoding="utf-8")))
    repo = cfg["repository"]
    fields = ("number,headRefOid,headRefName,baseRefName,isDraft,isCrossRepository,"
              "headRepository,reviewDecision,mergeStateStatus,statusCheckRollup")
    errors = 0
    for plugin, policy in sorted(cfg["plugins"].items()):
        try:
            prs = gh("pr", "list", "-R", repo, "--state", "open",
                     "--base", "plugin/" + plugin, "--limit", str(args.limit),
                     "--json", "number")
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            print(json.dumps({"plugin": plugin, "result": "list-error", "error": str(exc)}))
            errors += 1
            continue
        for item in prs:
            n = item["number"]
            try:
                pr = gh("pr", "view", str(n), "-R", repo, "--json", fields)
                reason = inspect(repo, plugin, policy["checks"], pr, args.apply)
            except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
                reason = "error:" + str(exc)
                errors += 1
            print(json.dumps({"plugin": plugin, "pr": n, "result": reason}))
    return int(bool(errors))

if __name__ == "__main__":
    sys.exit(main())
