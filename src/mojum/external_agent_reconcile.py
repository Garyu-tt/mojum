"""Offline, non-authoritative GitHub correlation of *verified* agent receipts.

Consumes ReceiptJournal.readback() from the separate authenticated #717 slice.
This module cannot authenticate receipts or GitHub snapshots by itself. Neither
GitHub activity nor a schedule constitutes an observed active process.
"""

from __future__ import annotations

import re

SCHEMA = "mojum.external-agent-correlation/1"
GITHUB_SCHEMA = "mojum.github-correlation-snapshot/1"
MAX_ITEMS = 1000
MAX_GITHUB_ITEMS = 2000
MAX_GITHUB_AGE_MS = 300_000
MAX_TIME_MS = 2**63 - 1
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_SOURCE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
_REPO = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}\Z", re.ASCII)
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z", re.ASCII)
_RUN_KEYS = frozenset({
    "source", "agent_id", "run_id", "state", "kind", "seq",
    "observed_ms", "age_ms", "ttl_ms", "repo", "host", "provider",
    "issue", "pr", "sha", "status_code",
})
_STATES = frozenset({"active", "stale", "finished", "failed"})
_STATUS = frozenset({"none", "completed", "failed", "cancelled", "rate_limited", "unreachable"})


class CorrelationError(ValueError):
    """Malformed, contradictory or unbounded correlation input."""


def _integer(value, field, low=0, high=MAX_TIME_MS):
    if type(value) is not int or not low <= value <= high:
        raise CorrelationError(f"invalid {field}")
    return value


def _name(value, field, pattern=_ID):
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise CorrelationError(f"invalid {field}")
    return value


def _fields(value, expected, what):
    if type(value) is not dict or set(value) != set(expected):
        raise CorrelationError(f"invalid {what} fields")


def _sequence(value, field, cap):
    if type(value) not in (list, tuple) or len(value) > cap:
        raise CorrelationError(f"invalid {field} collection")
    return value


def _number_or_none(value, field):
    return None if value is None else _integer(value, field, 1, 2**31 - 1)


def _validate_run(run, query_ms):
    _fields(run, _RUN_KEYS, "receipt run")
    for key, pattern in (("source", _SOURCE), ("agent_id", _ID),
                         ("run_id", _ID), ("repo", _REPO),
                         ("host", _ID), ("provider", _ID)):
        _name(run[key], key, pattern)
    _integer(run["seq"], "seq", 1, 1_000_000)
    seen = _integer(run["observed_ms"], "receipt observed_ms")
    age = _integer(run["age_ms"], "receipt age_ms")
    ttl = _integer(run["ttl_ms"], "receipt ttl_ms", 1_000, 300_000)
    if query_ms < seen or age != query_ms - seen:
        raise CorrelationError("inconsistent server-observed receipt age")
    if run["kind"] not in ("agent.started", "agent.heartbeat", "agent.finished", "agent.failed"):
        raise CorrelationError("invalid receipt kind")
    if type(run["state"]) is not str or run["state"] not in _STATES:
        raise CorrelationError("invalid receipt state")
    if type(run["status_code"]) is not str or run["status_code"] not in _STATUS:
        raise CorrelationError("invalid receipt status code")
    expected = ({"agent.finished": "finished", "agent.failed": "failed"}.get(run["kind"])
                or ("active" if age < ttl else "stale"))
    if run["state"] != expected:
        raise CorrelationError("receipt state inconsistent with terminal kind or TTL")
    if (run["kind"] in ("agent.started", "agent.heartbeat") and run["status_code"] != "none"
            or run["kind"] == "agent.finished" and run["status_code"] != "completed"
            or run["kind"] == "agent.failed" and run["status_code"] in ("none", "completed")):
        raise CorrelationError("invalid terminal diagnosis")
    _number_or_none(run["issue"], "issue")
    _number_or_none(run["pr"], "pr")
    if run["sha"] is not None:
        _name(run["sha"], "sha", _SHA)


def _validate_github(value, query_ms):
    if value is None:
        return None, None, None, "unavailable"
    _fields(value, {"schema", "repo", "observed_ms", "complete", "issues", "pulls"}, "GitHub snapshot")
    if value["schema"] != GITHUB_SCHEMA:
        raise CorrelationError("unsupported GitHub snapshot schema")
    repo = _name(value["repo"], "GitHub repo", _REPO)
    seen = _integer(value["observed_ms"], "GitHub observed_ms")
    if seen > query_ms:
        raise CorrelationError("GitHub snapshot from the future")
    if type(value["complete"]) is not bool:
        raise CorrelationError("invalid GitHub completeness")
    issues, pulls = {}, {}
    for issue in _sequence(value["issues"], "issues", MAX_GITHUB_ITEMS):
        _fields(issue, {"number", "state"}, "GitHub issue")
        number = _integer(issue["number"], "issue number", 1, 2**31 - 1)
        if issue["state"] not in ("open", "closed") or type(issue["state"]) is not str:
            raise CorrelationError("invalid issue state")
        if number in issues:
            raise CorrelationError("duplicate issue number")
        issues[number] = issue["state"]
    for pull in _sequence(value["pulls"], "pulls", MAX_GITHUB_ITEMS):
        _fields(pull, {"number", "state", "merged", "head_sha"}, "GitHub pull")
        number = _integer(pull["number"], "pull number", 1, 2**31 - 1)
        if type(pull["state"]) is not str or pull["state"] not in ("open", "closed"):
            raise CorrelationError("invalid pull state")
        if type(pull["merged"]) is not bool or (pull["merged"] and pull["state"] != "closed"):
            raise CorrelationError("invalid merged flag")
        if pull["head_sha"] is not None:
            _name(pull["head_sha"], "head_sha", _SHA)
        if number in pulls:
            raise CorrelationError("duplicate pull number")
        pulls[number] = pull
    freshness = "current" if query_ms - seen <= MAX_GITHUB_AGE_MS else "stale"
    return repo, issues, pulls, freshness


def reconcile(readback: dict, github: dict | None = None, *, schedules=()) -> dict:
    """Correlate trusted receipt readback with *unverified* GitHub metadata.

    Caller must source readback from authenticated ReceiptJournal.readback()
    and independently authenticate/fetch GitHub data; this adapter never makes
    network calls. GitHub metadata can annotate receipts, never change their
    active/stale/terminal status. Missing rows always mean UNKNOWN, not absent.
    """
    _fields(readback, {"observed_ms", "truncated", "runs"}, "receipt readback")
    now = _integer(readback["observed_ms"], "readback observed_ms")
    if type(readback["truncated"]) is not bool:
        raise CorrelationError("invalid readback truncated flag")
    runs = _sequence(readback["runs"], "runs", MAX_ITEMS)
    gh_repo, gh_issues, gh_pulls, freshness = _validate_github(github, now)
    schedule_ids = set()
    for schedule in _sequence(schedules, "schedules", MAX_ITEMS):
        _fields(schedule, {"source", "agent_id", "repo"}, "schedule")
        identity = (_name(schedule["source"], "source", _SOURCE),
                    _name(schedule["agent_id"], "agent_id"),
                    _name(schedule["repo"], "repo", _REPO))
        if identity in schedule_ids:
            raise CorrelationError("duplicate schedule identity")
        schedule_ids.add(identity)

    counts = {state: 0 for state in ("active", "stale", "finished", "failed")}
    result = []
    seen_ids = set()
    seen_agents = set()
    for run in runs:
        _validate_run(run, now)
        identity = (run["source"], run["agent_id"], run["run_id"])
        if identity in seen_ids:
            raise CorrelationError("duplicate authenticated run identity")
        seen_ids.add(identity)
        seen_agents.add((run["source"], run["agent_id"], run["repo"]))
        counts[run["state"]] += 1
        correlated = {"issue": None, "pr": None, "sha_match": None, "flags": []}
        if freshness == "current" and gh_repo == run["repo"]:
            issue_num, pr_num = run["issue"], run["pr"]
            if issue_num in gh_issues:
                correlated["issue"] = {
                    "state": gh_issues[issue_num],
                    "url": f"https://github.com/{gh_repo}/issues/{issue_num}",
                }
            if pr_num in gh_pulls:
                pull = gh_pulls[pr_num]
                correlated["pr"] = {
                    "state": "merged" if pull["merged"] else pull["state"],
                    "url": f"https://github.com/{gh_repo}/pull/{pr_num}",
                }
                if run["sha"] is not None and pull["head_sha"] is not None:
                    match = run["sha"] == pull["head_sha"]
                    correlated["sha_match"] = match
                    if not match:
                        correlated["flags"].append("receipt_sha_differs_from_pr_head")
                if pull["merged"] and run["state"] == "active":
                    correlated["flags"].append("merged_pr_does_not_finish_active_run")
        result.append({
            "source": run["source"], "agent_id": run["agent_id"],
            "run_id": run["run_id"], "repo": run["repo"],
            "state": run["state"], "age_ms": run["age_ms"],
            "issue_number": run["issue"], "pr_number": run["pr"],
            "scheduled": (run["source"], run["agent_id"], run["repo"]) in schedule_ids,
            "github": correlated,
        })
    result.sort(key=lambda x: (x["source"], x["agent_id"], x["run_id"]))
    schedule_only = [
        {"source": source, "agent_id": agent_id, "repo": repo,
         "state": "unknown", "reason": "schedule_without_authenticated_run"}
        for source, agent_id, repo in sorted(schedule_ids - seen_agents)
    ]
    return {
        "schema": SCHEMA, "observed_ms": now,
        "receipt_view_truncated": readback["truncated"],
        "github_freshness": freshness,
        "github_complete": github["complete"] if github is not None else False,
        "counts": {**counts, "schedule_only_unknown": len(schedule_only)},
        "runs": result, "schedule_only": schedule_only,
    }
