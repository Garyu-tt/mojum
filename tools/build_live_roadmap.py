"""Generate a minimal public Mojum roadmap snapshot using read-only GitHub REST."""
import datetime
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

REPO = "Garyu-tt/mojum"
ROOT = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "")


def get_pages(path):
    url = f"{ROOT}/repos/{REPO}/{path}"
    result = []
    while url:
        if not re.match(r"^https://api\.github\.com/(?:repos/Garyu-tt/mojum|repositories/1465824263)/", url, re.I):
            raise ValueError("Unexpected pagination host or repository")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "mojum-roadmap"}
        if TOKEN:
            headers["Authorization"] = f"Bearer {TOKEN}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
            payload = json.load(response)
            if not isinstance(payload, list):
                raise ValueError("Expected paginated array")
            result.extend(payload)
            match = re.search(r'<([^>]+)>;\s*rel="next"', response.headers.get("Link", ""))
            url = match.group(1) if match else None
    return result


def pick(record, fields):
    return {key: record.get(key) for key in fields.split()}


def issue(record):
    result = pick(record, "number title html_url state created_at closed_at")
    result["labels"] = [pick(label, "name") for label in record.get("labels", [])]
    result["milestone"] = pick(record["milestone"], "title") if record.get("milestone") else None
    result["pull_request"] = pick(record["pull_request"], "merged_at") if record.get("pull_request") else None
    # Keep references, not entire issue bodies or comments in the public snapshot.
    result["body"] = " ".join(dict.fromkeys(re.findall(r"(?:^|[\s(])(#\d+)\b", record.get("body") or "")))
    return result


def pull(record):
    result = pick(record, "number merged_at draft merge_commit_sha")
    for field in ("head", "base"):
        result[field] = pick(record.get(field) or {}, "ref")
    return result


def build():
    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "issues": [issue(x) for x in get_pages("issues?state=all&per_page=100")],
        "pulls": [pull(x) for x in get_pages("pulls?state=all&per_page=100")],
        "releases": [
            pick(x, "html_url name tag_name published_at created_at prerelease")
            for x in get_pages("releases?per_page=100") if not x.get("draft")
        ],
    }


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/roadmap/roadmap-data.json")
    snapshot = build()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Snapshot: {len(snapshot['issues'])} items, {len(snapshot['pulls'])} PRs, {len(snapshot['releases'])} releases")
