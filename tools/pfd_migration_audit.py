"""Read-only migration acceptance audit. Never executes PR code or merges."""
import argparse
import json
import re
import os
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}")
ROOT = Path(__file__).resolve().parents[1]

REPO = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+")
ENDPOINT = re.compile(r"(?:actions/runs/[1-9][0-9]*(?:/jobs\?per_page=100)?|git/commits/[0-9a-f]{40}|pulls/[1-9][0-9]*(?:/(?:files|reviews)\?per_page=100)?|commits/[0-9a-f]{40}/check-runs\?filter=all&per_page=100|branches\?protected=true&per_page=100)")
MAX_RESPONSE = 8 * 1024 * 1024
MAX_PAGES = 100

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("GitHub API redirect rejected")

def get_page(url):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {"Accept":"application/vnd.github+json",
               "X-GitHub-Api-Version":"2022-11-28",
               "User-Agent":"mojum-pfd-read-only"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(url, headers=headers, method="GET")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=30) as response:
        payload = response.read(MAX_RESPONSE + 1)
        if len(payload) > MAX_RESPONSE:
            raise ValueError("GitHub response exceeds budget")
        return json.loads(payload), response.headers.get("Link", "")

def api(repo, suffix, paginate=False):
    if (type(repo) is not str or not REPO.fullmatch(repo)
        or repo.split("/")[1] in (".", "..")
        or type(suffix) is not str or not ENDPOINT.fullmatch(suffix)):
        raise ValueError("unexpected GitHub API target")
    url = f"https://api.github.com/repos/{repo}/{suffix}"
    if not paginate:
        return get_page(url)[0]
    pages = []
    for page in range(1, MAX_PAGES + 1):
        # Never follow a server-provided URL with credentials.
        data, link = get_page(url + ("&" if "?" in url else "?") + f"page={page}")
        pages.append(data)
        if not re.search(r'rel="next"', link):
            return pages
    raise ValueError("GitHub pagination budget exceeded")

def flatten(pages):
    if not isinstance(pages,list) or any(not isinstance(p,list) for p in pages):
        raise ValueError("invalid paginated response")
    return [item for page in pages for item in page]

def evaluate(pr, lane, files, runs, reviews, protected):
    blockers=[]
    head=pr["head"]["sha"]
    base=pr["base"]["sha"]
    if (pr["state"]!="open" or pr["draft"] or
        pr["base"]["ref"]!=lane["base"] or pr["head"]["ref"]!=lane["head"] or
        pr["head"]["repo"]["full_name"]!=lane["repo"] or
        pr["base"]["repo"]["full_name"]!=lane["repo"] or
        not SHA.fullmatch(head) or not SHA.fullmatch(base)):
        blockers.append("unexpected-pr-identity")
    if head!=lane["candidate_sha"] or base!=lane["base_sha"]:
        blockers.append("pin-changed-reapproval-required")
    if pr.get("mergeable") is not True:
        blockers.append("mergeability-unconfirmed")
    if not files or len(files)!=pr["changed_files"] or any(
        f["filename"] not in lane["allowed_files"] or
        (f.get("previous_filename") and f["previous_filename"] not in lane["allowed_files"])
        for f in files):
        blockers.append("scope-unverified")
    for name in lane["checks"]:
        matching=[r for r in runs if r.get("name")==name and
                  (r.get("app") or {}).get("id")==15368]
        if len(matching)!=1 or matching[0].get("status")!="completed" or matching[0].get("conclusion")!="success":
            blockers.append("check-unverified:"+name)
    # Positive reviews are advisory only; no author/committer/pusher
    # independence or trusted workflow provenance is established here.
    if not any(r.get("state")=="APPROVED" and r.get("commit_id")==head and
               (r.get("user") or {}).get("login")!=pr["user"]["login"]
               for r in reviews):
        blockers.append("head-review-missing")
    if not protected:
        blockers.append("target-protection-not-observed")
    blockers.extend(["trusted-ci-provenance-not-verified","independent-review-provenance-not-verified"])
    return {"pr":pr["number"],"head":head,"base":base,"decision":"HOLD",
            "blockers":blockers,"checks_seen":len(runs)}

def observe_run(pr, lane, run, jobs, merge):
    """Correlate server metadata, not runner execution or workflow trust."""
    head, base = lane["candidate_sha"], lane["base_sha"]
    expected = lane["observed_run"]
    reasons = []
    if (pr["head"]["sha"] != head or pr["base"]["sha"] != base
        or pr.get("merge_commit_sha") != expected["merge_sha"]
        or merge.get("sha") != expected["merge_sha"]
        or [p.get("sha") for p in merge.get("parents", [])] != [base, head]):
        reasons.append("merge-parent-mismatch")
    links = [p for p in run.get("pull_requests", [])
             if p.get("number") == lane["pr"] and
             (p.get("head") or {}).get("sha") == head and
             (p.get("base") or {}).get("sha") == base]
    if (run.get("id") != expected["id"] or run.get("path") != expected["workflow"]
        or run.get("event") != "pull_request" or run.get("head_sha") != head
        or run.get("status") != "completed" or run.get("conclusion") != "success"
        or len(links) != 1):
        reasons.append("workflow-run-mismatch")
    for name in lane["checks"]:
        matches = [j for j in jobs if j.get("name") == name]
        if len(matches) != 1 or not all(
            j.get("run_id") == expected["id"] and j.get("head_sha") == head
            and j.get("status") == "completed" and j.get("conclusion") == "success"
            and sum(step.get("name") == expected["test_step"] and
                    step.get("conclusion") == "success" for step in j.get("steps", [])) == 1
            for j in matches):
            reasons.append("job-metadata-mismatch:"+name)
    return {"run_id":expected["id"],"merge_sha":expected["merge_sha"],
            "metadata_consistent":not reasons,"reasons":reasons,
            "trust_verified":False}

def audit(repo,lane):
    n=lane["pr"]
    if type(n) is not int or n < 1:
        raise ValueError("invalid PR number")
    pr=api(repo,f"pulls/{n}")
    files=flatten(api(repo,f"pulls/{n}/files?per_page=100",True))
    reviews=flatten(api(repo,f"pulls/{n}/reviews?per_page=100",True))
    pages=api(repo,f"commits/{pr['head']['sha']}/check-runs?filter=all&per_page=100",True)
    if not isinstance(pages,list) or any("check_runs" not in p for p in pages):
        raise ValueError("invalid check pagination")
    runs=[r for page in pages for r in page["check_runs"]]
    branches=flatten(api(repo,"branches?protected=true&per_page=100",True))
    result = evaluate(pr,lane,files,runs,reviews,any(b["name"]==lane["base"] for b in branches))
    observed = lane.get("observed_run")
    if observed:
        run_id = observed["id"]
        if type(run_id) is not int or run_id < 1:
            raise ValueError("invalid run ID")
        run = api(repo,f"actions/runs/{run_id}")
        job_pages = api(repo,f"actions/runs/{run_id}/jobs?per_page=100",True)
        jobs = [j for page in job_pages for j in page["jobs"]]
        merge = api(repo,f"git/commits/{observed['merge_sha']}")
        result["observed_run"] = observe_run(pr,lane,run,jobs,merge)
        if not result["observed_run"]["metadata_consistent"]:
            result["blockers"].append("run-metadata-inconsistent")
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--policy",type=Path,default=ROOT/"pfd/migration_audit.json")
    args=p.parse_args()
    cfg=json.loads(args.policy.read_text(encoding="utf-8"))
    errors=0
    for lane in cfg["lanes"]:
        try:
            print(json.dumps(audit(cfg["repository"],lane),sort_keys=True))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            errors+=1
            print(json.dumps({"pr":lane["pr"],"decision":"HOLD","error":str(exc)}))
    return int(bool(errors))

if __name__=="__main__":
    raise SystemExit(main())
