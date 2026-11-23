"""Read-only GitHub Projects v2 reconciliation for the Mojum D3 board.

Nothing here writes Project items, fields, status values or repository state.
The optional live reader uses the operator's *existing* gh authentication;
credentials are never accepted as arguments or persisted.  Offline snapshots
make the planning/audit path fully testable without GitHub access.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

OWNER = "Garyu-tt"
REPO = "Garyu-tt/mojum"
PROJECT_NUMBER = 1
PROJECT_URL = "https://github.com/users/Garyu-tt/projects/1"
MAX_PAGES = 50
MAX_ITEMS = 5000
MAX_BYTES = 2 * 1024 * 1024
SCHEMA = "mojum.projects-v2.board-audit/1"

# GraphQL selection is intentionally limited to board metadata and item IDs.
# No item titles, bodies, comments, or non-target repository content are read.
QUERY = """query($login:String!,$number:Int!,$cursor:String){
  user(login:$login){projectV2(number:$number){
    id number title url
    fields(first:100){pageInfo{hasNextPage} nodes{
      ... on ProjectV2SingleSelectField{id name options{id name}}
    }}
    items(first:100,after:$cursor){pageInfo{hasNextPage endCursor} nodes{
      id content{
        __typename
        ... on Issue{id number url repository{nameWithOwner}}
        ... on PullRequest{id number url repository{nameWithOwner}}
      }
      fieldValues(first:30){pageInfo{hasNextPage} nodes{
        ... on ProjectV2ItemFieldSingleSelectValue{
          optionId field{... on ProjectV2SingleSelectField{id name}}
        }
      }}
    }}
  }}
}"""


class BoardError(ValueError):
    """The board snapshot or candidate set cannot be reconciled safely."""


def _object(value: Any, label: str) -> dict:
    if type(value) is not dict:
        raise BoardError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list:
    if type(value) is not list:
        raise BoardError(f"{label} must be an array")
    return value


def _string(value: Any, label: str) -> str:
    if type(value) is not str or not value or len(value) > 1024:
        raise BoardError(f"{label} must be a bounded nonempty string")
    return value


def _number(value: Any, label: str) -> int:
    if type(value) is not int or not 1 <= value <= 1_000_000_000:
        raise BoardError(f"{label} must be a positive integer")
    return value


def _connection(value: Any, label: str) -> tuple[list, dict]:
    conn = _object(value, label)
    nodes = _list(conn.get("nodes"), f"{label}.nodes")
    page_info = _object(conn.get("pageInfo"), f"{label}.pageInfo")
    if type(page_info.get("hasNextPage")) is not bool:
        raise BoardError(f"{label} missing pagination boolean")
    return nodes, page_info


def _project_from_page(raw: Any) -> dict:
    envelope = _object(raw, "GraphQL response")
    if envelope.get("errors"):
        raise BoardError("GitHub GraphQL reported errors")
    project = _object(_object(_object(envelope.get("data"), "data").get("user"), "user").get("projectV2"), "projectV2")
    if _number(project.get("number"), "project number") != PROJECT_NUMBER:
        raise BoardError("wrong project number")
    if _string(project.get("url"), "project url").rstrip("/") != PROJECT_URL:
        raise BoardError("wrong project owner or URL")
    return project


def _status_fields(project: dict) -> tuple[str, list[dict]]:
    fields, info = _connection(project.get("fields"), "fields")
    if info["hasNextPage"]:
        raise BoardError("board fields are not fully paginated")
    statuses = [field for field in fields if type(field) is dict and field.get("name") == "Status" and "options" in field]
    if len(statuses) != 1:
        raise BoardError("expected exactly one existing Status select field")
    field = statuses[0]
    field_id = _string(field.get("id"), "Status field id")
    options = _list(field.get("options"), "Status options")
    if not options or len(options) > 100:
        raise BoardError("invalid Status options count")
    ids: set[str] = set()
    names: set[str] = set()
    result = []
    for option in options:
        option = _object(option, "Status option")
        oid = _string(option.get("id"), "option id")
        name = _string(option.get("name"), "option name")
        if oid in ids or name.casefold() in names:
            raise BoardError("duplicate Status option identifier/name")
        ids.add(oid)
        names.add(name.casefold())
        result.append({"id": oid, "name": name})
    return field_id, result


def reconcile(pages: list[dict], candidates: list[dict] | None = None) -> dict:
    """Deterministic no-write audit. No inferred status changes or silent adds."""
    pages = _list(pages, "pages")
    if not 1 <= len(pages) <= MAX_PAGES:
        raise BoardError("invalid page count")
    seen_items: set[str] = set()
    seen_cursors: set[str] = set()
    records: dict[tuple[str, int], list[dict]] = {}
    ignored = 0
    previous = None
    for index, raw in enumerate(pages):
        project = _project_from_page(raw)
        identity = (_string(project.get("id"), "project id"), _string(project.get("title"), "project title"))
        field_id, options = _status_fields(project)
        if previous is not None and previous != (identity, field_id, options):
            raise BoardError("board metadata changed across pages")
        previous = (identity, field_id, options)
        nodes, page_info = _connection(project.get("items"), "items")
        if len(nodes) > 100 or len(seen_items) + len(nodes) > MAX_ITEMS:
            raise BoardError("board item budget exceeded")
        if page_info["hasNextPage"] != (index < len(pages) - 1):
            raise BoardError("incomplete or excessive page sequence")
        if page_info["hasNextPage"]:
            cursor = _string(page_info.get("endCursor"), "next page cursor")
            if cursor in seen_cursors:
                raise BoardError("repeated page cursor")
            seen_cursors.add(cursor)
        allowed = {option["id"] for option in options}
        for value in nodes:
            item = _object(value, "item")
            iid = _string(item.get("id"), "project item id")
            if iid in seen_items:
                raise BoardError("repeated project item ID")
            seen_items.add(iid)
            content = item.get("content")
            if content is None:
                ignored += 1
                continue
            content = _object(content, "item content")
            kind = {"Issue": "issue", "PullRequest": "pr"}.get(content.get("__typename"))
            if kind is None:
                ignored += 1
                continue
            repository = _object(content.get("repository"), "item repository")
            if _string(repository.get("nameWithOwner"), "item repository") != REPO:
                ignored += 1
                continue
            number = _number(content.get("number"), "item number")
            url = _string(content.get("url"), "item URL")
            expected = f"https://github.com/{REPO}/{'issues' if kind == 'issue' else 'pull'}/{number}"
            if url != expected:
                raise BoardError("target item URL/repository mismatch")
            values, info = _connection(item.get("fieldValues"), "item field values")
            if info["hasNextPage"]:
                raise BoardError("incomplete field values; status cannot be audited")
            status = None
            for val in values:
                if type(val) is not dict or not isinstance(val.get("field"), dict):
                    continue
                if val["field"].get("id") != field_id:
                    continue
                if status is not None:
                    raise BoardError("multiple Status values on one item")
                status = _string(val.get("optionId"), "item Status option")
                if status not in allowed:
                    raise BoardError("item references unknown Status option")
            records.setdefault((kind, number), []).append({"item_id": iid, "status_option_id": status})
    assert previous is not None
    identity, field_id, options = previous
    existing = [{"kind": kind, "number": number, **entry}
                for (kind, number), entries in sorted(records.items()) for entry in entries]
    duplicates = [{"kind": kind, "number": number, "count": len(entries)}
                  for (kind, number), entries in sorted(records.items()) if len(entries) > 1]
    missing = []
    if candidates is not None:
        candidates = _list(candidates, "candidates")
        if len(candidates) > MAX_ITEMS:
            raise BoardError("candidate budget exceeded")
        seen_candidates: set[tuple[str, int]] = set()
        for candidate in candidates:
            candidate = _object(candidate, "candidate")
            if set(candidate) != {"kind", "number", "url"}:
                raise BoardError("candidate fields must be kind, number, url only")
            kind = candidate["kind"]
            if type(kind) is not str or kind not in ("issue", "pr"):
                raise BoardError("invalid candidate kind")
            number = _number(candidate["number"], "candidate number")
            expected = f"https://github.com/{REPO}/{'issues' if kind == 'issue' else 'pull'}/{number}"
            if candidate["url"] != expected:
                raise BoardError("candidate outside target repository")
            key = (kind, number)
            if key in seen_candidates:
                raise BoardError("duplicate input candidate")
            seen_candidates.add(key)
            if key not in records:
                missing.append({"kind": kind, "number": number, "url": expected})
    return {
        "schema": SCHEMA, "mode": "read_only", "project_url": PROJECT_URL,
        "project_id": identity[0], "project_title": identity[1],
        "status_field_id": field_id, "status_options": options,
        "target_items": existing, "ignored_non_target_items": ignored,
        "duplicate_target_cards": duplicates, "missing_target_cards": missing,
        "action": "manual_review_required" if duplicates else "preview_only",
    }


def _load_json(path: Path) -> Any:
    # A pre-open stat is not a byte limit: a path can be swapped after stat.
    # Bound the actual read on the opened handle before decoding or parsing.
    try:
        if not path.is_file():
            raise BoardError("missing or oversized input JSON")
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise BoardError("unreadable input JSON") from exc
    if len(raw) > MAX_BYTES:
        raise BoardError("missing or oversized input JSON")
    try:
        return json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise BoardError("invalid input JSON") from exc


def _live_pages() -> list[dict]:
    pages = []
    seen: set[str] = set()
    cursor = None
    for _ in range(MAX_PAGES):
        args = ["gh", "api", "graphql", "-f", "query=" + QUERY,
                "-f", "login=" + OWNER, "-F", "number=" + str(PROJECT_NUMBER)]
        if cursor is not None:
            args.extend(["-f", "cursor=" + cursor])
        try:
            done = subprocess.run(args, capture_output=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BoardError("GitHub CLI unavailable or GraphQL request timed out") from exc
        if done.returncode != 0 or len(done.stdout) > MAX_BYTES:
            raise BoardError("GitHub Projects v2 read denied, failed, or exceeded byte budget")
        try:
            page = json.loads(done.stdout)
        except ValueError as exc:
            raise BoardError("invalid GitHub GraphQL JSON") from exc
        project = _project_from_page(page)
        _, info = _connection(project.get("items"), "items")
        pages.append(page)
        if not info["hasNextPage"]:
            return pages
        cursor = _string(info.get("endCursor"), "next page cursor")
        if cursor in seen:
            raise BoardError("repeated live page cursor")
        seen.add(cursor)
    raise BoardError("GitHub Project exceeds page limit")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Mojum GitHub Project #1 status audit")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--snapshot", type=Path, help="offline saved GitHub GraphQL JSON (single page or page array)")
    src.add_argument("--live", action="store_true", help="opt-in gh CLI authenticated GraphQL read")
    parser.add_argument("--candidates", type=Path, help="optional target repo issue/PR candidates JSON")
    args = parser.parse_args(argv)
    try:
        pages = _live_pages() if args.live else _load_json(args.snapshot)
        if type(pages) is dict:
            pages = [pages]
        candidate_set = _load_json(args.candidates) if args.candidates else None
        report = reconcile(pages, candidate_set)
    except BoardError as exc:
        print(f"PROJECT_BOARD_AUDIT_ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 1 if report["duplicate_target_cards"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
