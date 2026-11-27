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

def counted_rows(pages, key):
    if type(pages) is not list or not pages:
        raise ValueError("invalid counted pagination: "+key)
    rows=[]
    total=None
    seen=set()
    for page in pages:
        if type(page) is not dict or type(page.get("total_count")) is not int or page["total_count"] < 0 or type(page.get(key)) is not list:
            raise ValueError("invalid counted pagination: "+key)
        if total is None:
            total=page["total_count"]
        if total != page["total_count"]:
            raise ValueError("inconsistent pagination count: "+key)
        for row in page[key]:
            if type(row) is not dict or type(row.get("id")) is not int or row["id"] < 1 or row["id"] in seen:
                raise ValueError("invalid or duplicate pagination ID: "+key)
            seen.add(row["id"])
            rows.append(row)
    if len(rows)!=total:
        raise ValueError("incomplete pagination: "+key)
    return rows

def evaluate(pr, lane, files, runs, reviews, protected):
    blockers=[]
    head=pr["head"]["sha"]
    base=pr["base"]["sha"]
    if (type(pr["number"]) is not int or pr["number"] != lane["pr"] or
        pr["state"]!="open" or
        pr["base"]["ref"]!=lane["base"] or pr["head"]["ref"]!=lane["head"] or
        pr["head"]["repo"]["full_name"]!=lane["repo"] or
        pr["base"]["repo"]["full_name"]!=lane["repo"] or
        not SHA.fullmatch(head) or not SHA.fullmatch(base)):
        blockers.append("unexpected-pr-identity")
    if pr["draft"]:
        blockers.append("draft-not-ready")
    if head!=lane["candidate_sha"] or base!=lane["base_sha"]:
        blockers.append("pin-changed-reapproval-required")
    if pr.get("mergeable") is not True:
        blockers.append("mergeability-unconfirmed")
    if not files or len(files)!=pr["changed_files"] or len({f["filename"] for f in files}) != len(files) or any(
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
               type((r.get("user") or {}).get("login")) is str and
               bool((r.get("user") or {}).get("login")) and
               (r.get("user") or {}).get("login")!=pr["user"]["login"]
               for r in reviews):
        blockers.append("head-review-missing")
    if not protected:
        blockers.append("target-protection-not-observed")
    blockers.extend(["trusted-ci-provenance-not-verified","independent-review-provenance-not-verified"])
    return {"pr":pr["number"],"head":head,"base":base,"decision":"HOLD",
            "blockers":blockers,"checks_seen":len(runs)}

def observe_run(pr, lane, run, jobs, merge, *, observed=None):
    """Correlate server metadata, not runner execution or workflow trust."""
    head, base = lane["candidate_sha"], lane["base_sha"]
    expected = observed if observed is not None else lane["observed_run"]
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
    for name in expected.get("checks", lane["checks"]):
        matches = [j for j in jobs if j.get("name") == name]
        step_policy = expected.get("job_steps", {})
        required_steps = (step_policy.get(name, [expected.get("test_step")])
                          if type(step_policy) is dict else None)
        if (type(required_steps) is not list or not required_steps
            or any(type(step) is not str or not step.strip() for step in required_steps)
            or len(set(required_steps)) != len(required_steps)):
            reasons.append("invalid-job-step-policy:"+name)
            continue
        if len(matches) != 1 or not all(
            j.get("run_id") == expected["id"] and j.get("head_sha") == head
            and j.get("status") == "completed" and j.get("conclusion") == "success"
            and all(sum(step.get("name") == required for step in j.get("steps", [])) == 1
                    and any(step.get("name") == required and step.get("status") == "completed"
                            and step.get("conclusion") == "success"
                            for step in j.get("steps", []))
                    for required in required_steps)
            for j in matches):
            reasons.append("job-metadata-mismatch:"+name)
    return {"run_id":expected["id"],"merge_sha":expected["merge_sha"],
            "metadata_consistent":not reasons,"reasons":reasons,
            "trust_verified":False}

def pr_identity(pr):
    """Fields on which the collected evidence depends, not mutable PR prose."""
    return (pr['number'], pr['state'], pr['draft'], pr.get('mergeable'),
            pr.get('merge_commit_sha'), pr['changed_files'], pr['user']['login'],
            *((pr[side]['ref'], pr[side]['sha'], pr[side]['repo']['full_name'])
              for side in ('head', 'base')))

def audit(repo,lane):
    n=lane["pr"]
    if type(n) is not int or n < 1:
        raise ValueError("invalid PR number")
    pr=api(repo,f"pulls/{n}")
    files=flatten(api(repo,f"pulls/{n}/files?per_page=100",True))
    reviews=flatten(api(repo,f"pulls/{n}/reviews?per_page=100",True))
    pages=api(repo,f"commits/{pr['head']['sha']}/check-runs?filter=all&per_page=100",True)
    runs=counted_rows(pages,"check_runs")
    branches=flatten(api(repo,"branches?protected=true&per_page=100",True))
    observations = lane.get("observed_runs")
    if observations is None:
        observations = [lane["observed_run"]] if "observed_run" in lane else []
    scoped_ids = set()
    reports = []
    expected_names = []
    for observed in observations:
        run_id = observed["id"]
        if type(run_id) is not int or run_id < 1:
            raise ValueError("invalid run ID")
        run = api(repo,f"actions/runs/{run_id}")
        job_pages = api(repo,f"actions/runs/{run_id}/jobs?per_page=100",True)
        jobs = counted_rows(job_pages,"jobs")
        merge = api(repo,f"git/commits/{observed['merge_sha']}")
        reports.append(observe_run(pr,lane,run,jobs,merge,observed=observed))
        expected_names.extend(observed.get("checks",lane["checks"]))
        scoped_ids.update(j["id"] for j in jobs if j.get("name") in observed.get("checks",lane["checks"]))
    # Historical jobs on the same head are not evidence for a retargeted PR.
    current_checks = [r for r in runs if r.get("id") in scoped_ids] if observations else runs
    result = evaluate(pr,lane,files,current_checks,reviews,any(b["name"]==lane["base"] for b in branches))
    if observations:
        result["observed_runs"] = reports
        if sorted(expected_names) != sorted(lane["checks"]):
            result["blockers"].append("run-coverage-mismatch")
        if not all(r["metadata_consistent"] for r in reports):
            result["blockers"].append("run-metadata-inconsistent")
    # REST pages are not atomic: discard evidence if PR identity moved.
    if pr_identity(api(repo,f"pulls/{n}")) != pr_identity(pr):
        raise ValueError("PR identity changed during audit; retry")
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--policy",type=Path,default=ROOT/"pfd/migration_audit.json")
    args=p.parse_args()
    cfg=json.loads(args.policy.read_text(encoding="utf-8"))
    errors=0
    for lane in cfg["lanes"]:
        number = lane.get("pr") if type(lane) is dict else None
        if type(number) is not int or number < 1:
            errors+=1
            print(json.dumps({"pr":None,"decision":"HOLD","error":"invalid policy lane PR number"}))
            continue
        try:
            print(json.dumps(audit(cfg["repository"],lane),sort_keys=True))
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
            errors+=1
            print(json.dumps({"pr":number,"decision":"HOLD","error":str(exc)}))
    return int(bool(errors))

if __name__=="__main__":
    raise SystemExit(main())
