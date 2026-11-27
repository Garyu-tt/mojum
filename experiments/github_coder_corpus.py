"""Collect merged GitHub PR episodes; data only, never run downloaded code.

Example:
  python experiments/github_coder_corpus.py Garyu-tt/mojum --out corpus/mojum --limit 100
Use only repositories you own or have approved for training. Review license,
sensitive content and data retention before training or redistributing.
"""
import argparse
import json
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote

API = "https://api.github.com"


def get(path, **params):
    url = API + path + ("?" + urlencode(params) if params else "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "connectome-coder-corpus"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def pages(path, **params):
    for page in range(1, 101):
        items = get(path, per_page=100, page=page, **params)
        yield from items
        if len(items) < 100:
            break


def episode(repo, pr, files):
    number = pr["number"]
    # Body/review may reveal the solution. Keep it on the teacher side.
    before = {"id": f"{repo}#{number}", "repo": repo, "base_sha": pr["base"]["sha"],
              "prompt": pr["title"], "source": pr["html_url"]}
    after = {"id": before["id"], "head_sha": pr["head"]["sha"],
             "merge_sha": pr["merge_commit_sha"], "merged_at": pr["merged_at"],
             "teacher_notes": pr.get("body"),
             "files": [{"path": f["filename"], "status": f["status"],
                        "previous_path": f.get("previous_filename"), "patch": f.get("patch")}
                       for f in files],
             "missing_patches": [f["filename"] for f in files if not f.get("patch")],
             "verification": "merged_only_not_tested"}
    return before, after


def collect(repo, out, limit):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("expected owner/repository")
    path = "/repos/" + repo
    meta = get(path)
    if meta.get("private"):
        raise ValueError("private repository collection disabled")
    out.mkdir(parents=True, exist_ok=False)
    manifest = {"repo": repo, "license": (meta.get("license") or {}).get("spdx_id"),
                "count": 0, "complete": False,
                "warning": "titles may reveal solutions; review all examples; no automatic training"}
    try:
        with (out/"student.jsonl").open("x", encoding="utf-8") as student, \
             (out/"teacher.jsonl").open("x", encoding="utf-8") as teacher:
            for item in pages(path+"/pulls", state="closed", sort="updated", direction="desc"):
                pr = get(path+"/pulls/"+str(item["number"]))
                if not pr.get("merged_at"):
                    continue
                files = list(pages(path+"/pulls/"+str(pr["number"])+"/files"))
                if len(files) != pr.get("changed_files"):
                    continue
                before, after = episode(repo, pr, files)
                student.write(json.dumps(before, ensure_ascii=False)+"\n")
                teacher.write(json.dumps(after, ensure_ascii=False)+"\n")
                manifest["count"] += 1
                if manifest["count"] >= limit:
                    break
        manifest["complete"] = True
    finally:
        (out/"manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    print(json.dumps(collect(args.repo, args.out, args.limit), indent=2))
