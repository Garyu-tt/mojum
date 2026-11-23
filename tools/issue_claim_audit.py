"""Offline, read-only conflict audit for explicit Mojum issue-scope claims.

This tool cannot make reservations atomic, verify GitHub identity, or authorize a
merge. It only checks a bounded, caller-supplied snapshot for contradictions.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

INPUT_SCHEMA = "mojum.issue-claim-export/1"
REPORT_SCHEMA = "mojum.issue-claim-audit/1"
MAX_BYTES = 2 * 1024 * 1024
MAX_ISSUES = 300
MAX_COMMENTS = 20000
MAX_BODY_BYTES = 16384
MAX_MARKERS_PER_SOURCE = 1
# Only up to 3 spaces: a four-space indent is a Markdown code example.
CLAIM_MARKER = re.compile(r"^ {0,3}<!-- mojum-claim/1: (\{[^\r\n]*\}) -->[ \t]*$", re.M)
FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
MARKER_HINT = re.compile(r"mojum-claim/1", re.I)
LEGACY_CLAIM_HINT = re.compile(r"(?:🔒|\bCLAIMED\b|\bCLAIM\s*/\s*IN\s+PROGRESS\b|\bCLAIM\s+UPDATE\b)", re.I)
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class ClaimAuditError(ValueError):
    """Input is malformed; never report its claims as collision-free."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ClaimAuditError("duplicate JSON key")
        obj[key] = value
    return obj


def _reject_constant(_value: str) -> None:
    raise ClaimAuditError("non-finite JSON numbers are not allowed")


def _positive_id(value: Any, what: str) -> int:
    if type(value) is not int or value < 1:
        raise ClaimAuditError(f"{what} must be a positive integer")
    return value


def _name(value: Any, what: str) -> str:
    if not isinstance(value, str) or TOKEN.fullmatch(value) is None:
        raise ClaimAuditError(f"{what} must be a nonempty bounded identifier")
    return value


def _body(value: Any) -> str:
    if not isinstance(value, str):
        raise ClaimAuditError("body must be UTF-8 text")
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeError as exc:
        raise ClaimAuditError("body must be valid UTF-8 text") from exc
    if size > MAX_BODY_BYTES:
        raise ClaimAuditError("body must be text of at most 16 KiB")
    return value


def _time(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 35:
        raise ClaimAuditError("created_at must be an RFC3339 UTC timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClaimAuditError("invalid created_at timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() != timezone.utc.utcoffset(dt):
        raise ClaimAuditError("created_at must be UTC")
    return dt.astimezone(timezone.utc)


def _inside_fenced_code(body: str, offset: int) -> bool:
    """Fail closed on markers quoted inside Markdown code fences."""
    fence: tuple[str, int] | None = None
    for line in body[:offset].splitlines():
        match = FENCE_LINE.match(line)
        if match is None:
            continue
        marker, rest = match.groups()
        if fence is None:
            fence = (marker[0], len(marker))
        elif marker[0] == fence[0] and len(marker) >= fence[1] and not rest.strip():
            fence = None
    return fence is not None


def _marker_payload(body: str) -> tuple[dict[str, str] | None, bool]:
    """Return exact standalone claim marker, and whether any untrusted hint exists."""
    matches = list(CLAIM_MARKER.finditer(body))
    if len(matches) > MAX_MARKERS_PER_SOURCE:
        raise ClaimAuditError("at most one claim marker per source")
    # A malformed or quoted marker must not be mistaken for a clean audit.
    if len(MARKER_HINT.findall(body)) != len(matches):
        raise ClaimAuditError("malformed or non-standalone claim marker")
    if matches and _inside_fenced_code(body, matches[0].start()):
        raise ClaimAuditError("claim marker inside fenced code example")
    if not matches:
        return None, bool(LEGACY_CLAIM_HINT.search(body))
    try:
        payload = json.loads(matches[0].group(1), object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant)
    except (ValueError, TypeError) as exc:
        raise ClaimAuditError("invalid claim marker JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"worker", "scope", "action"}:
        raise ClaimAuditError("claim marker must contain exactly worker, scope and action")
    worker = _name(payload["worker"], "worker")
    scope = _name(payload["scope"], "scope")
    action = payload["action"]
    if action not in ("claim", "release"):
        raise ClaimAuditError("action must be claim or release")
    return {"worker": worker, "scope": scope, "action": action}, False


def audit(snapshot: Any) -> dict[str, Any]:
    """Deterministic advisory audit; never consult GitHub or mutate any resource."""
    if not isinstance(snapshot, dict) or snapshot.get("schema") != INPUT_SCHEMA:
        raise ClaimAuditError("unsupported input schema")
    repository = snapshot.get("repository")
    if not isinstance(repository, str) or REPO.fullmatch(repository) is None:
        raise ClaimAuditError("invalid repository full name")
    issues = snapshot.get("issues")
    if not isinstance(issues, list) or len(issues) > MAX_ISSUES:
        raise ClaimAuditError("issues must be a list with at most 300 entries")
    numbers: set[int] = set()
    seen_comments: set[int] = set()  # GitHub comment IDs are global across issues.
    total_comments = 0
    results: list[dict[str, Any]] = []
    for item in issues:
        if not isinstance(item, dict):
            raise ClaimAuditError("issue must be an object")
        number = _positive_id(item.get("number"), "issue number")
        if number in numbers:
            raise ClaimAuditError("duplicate issue number")
        numbers.add(number)
        comments = item.get("comments", [])
        if not isinstance(comments, list):
            raise ClaimAuditError("comments must be a list")
        total_comments += len(comments)
        if total_comments > MAX_COMMENTS:
            raise ClaimAuditError("too many comments")
        sources: list[tuple[datetime, int, str, str]] = []
        if "body" in item:
            sources.append((_time(item.get("created_at")), 0,
                            _name(item.get("author"), "issue author"), _body(item["body"])))
        for comment in comments:
            if not isinstance(comment, dict):
                raise ClaimAuditError("comment must be an object")
            comment_id = _positive_id(comment.get("id"), "comment id")
            if comment_id in seen_comments:
                raise ClaimAuditError("duplicate comment id")
            seen_comments.add(comment_id)
            sources.append((_time(comment.get("created_at")), comment_id,
                            _name(comment.get("author"), "comment author"), _body(comment.get("body"))))
        # Comments are immutable timeline observations in this snapshot; the issue
        # body is treated as source id 0, but timestamp order remains decisive.
        sources.sort(key=lambda source: (source[0], source[1]))
        active: dict[tuple[str, str], dict[str, Any]] = {}
        manual_review: list[dict[str, Any]] = []
        for _created, source_id, actor, text in sources:
            marker, unknown = _marker_payload(text)
            if unknown:
                manual_review.append({"source_id": source_id,
                                      "reason": "UNSTRUCTURED_CLAIM_HINT"})
            if marker is None:
                continue
            key = marker["scope"], marker["worker"]
            if marker["action"] == "claim":
                existing = active.get(key)
                if existing is not None:
                    if existing["actor"] != actor:
                        manual_review.append({"source_id": source_id,
                                              "reason": "WORKER_ACTOR_MISMATCH"})
                    # Repeated claims from the same worker do not create false
                    # multi-worker collisions; original claim remains the owner.
                    continue
                active[key] = {"scope": marker["scope"], "worker": marker["worker"],
                               "actor": actor, "source_id": source_id}
            else:
                existing = active.get(key)
                if existing is None:
                    manual_review.append({"source_id": source_id,
                                          "reason": "UNMATCHED_RELEASE"})
                elif existing["actor"] != actor:
                    manual_review.append({"source_id": source_id,
                                          "reason": "RELEASE_ACTOR_MISMATCH"})
                else:
                    del active[key]
        by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in active.values():
            by_scope[record["scope"]].append(record)
        collisions = [{"scope": scope,
                       "workers": sorted(r["worker"] for r in records)}
                      for scope, records in sorted(by_scope.items()) if len(records) > 1]
        results.append({"number": number,
                        "active_claims": sorted(active.values(), key=lambda r: (r["scope"], r["worker"])),
                        "collisions": collisions, "manual_review": manual_review})
    results.sort(key=lambda item: item["number"])
    collision_count = sum(len(item["collisions"]) for item in results)
    review_count = sum(len(item["manual_review"]) for item in results)
    status = "collision" if collision_count else ("needs_review" if review_count else "clear")
    return {"schema": REPORT_SCHEMA, "repository": repository,
            "status": status, "collision_scopes": collision_count,
            "manual_review_sources": review_count, "issues": results,
            "reservation_performed": False, "merge_authorized": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline advisory Mojum issue claim collision audit")
    parser.add_argument("--input", required=True, type=Path, help="bounded GitHub issue/comments JSON export")
    args = parser.parse_args(argv)
    try:
        with args.input.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ClaimAuditError("input exceeds 2 MiB")
        snapshot = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
        report = audit(snapshot)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(json.dumps({"schema": REPORT_SCHEMA, "status": "invalid", "error": str(exc),
                          "reservation_performed": False, "merge_authorized": False},
                         sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0 if report["status"] == "clear" else 1


if __name__ == "__main__":
    raise SystemExit(main())
