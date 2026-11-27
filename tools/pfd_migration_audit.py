"""Read-only migration acceptance audit. Never executes PR code or merges."""
import argparse
import json
import re
import subprocess
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}")
ROOT = Path(__file__).resolve().parents[1]

def api(repo, suffix, paginate=False):
    args=["gh","api",f"repos/{repo}/{suffix}"]
    if paginate: args += ["--paginate","--slurp"]
    return json.loads(subprocess.check_output(args,text=True,timeout=90))

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

def audit(repo,lane):
    n=lane["pr"]
    pr=api(repo,f"pulls/{n}")
    files=flatten(api(repo,f"pulls/{n}/files?per_page=100",True))
    reviews=flatten(api(repo,f"pulls/{n}/reviews?per_page=100",True))
    pages=api(repo,f"commits/{pr['head']['sha']}/check-runs?filter=all&per_page=100",True)
    if not isinstance(pages,list) or any("check_runs" not in p for p in pages):
        raise ValueError("invalid check pagination")
    runs=[r for page in pages for r in page["check_runs"]]
    branches=flatten(api(repo,"branches?protected=true&per_page=100",True))
    return evaluate(pr,lane,files,runs,reviews,any(b["name"]==lane["base"] for b in branches))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--policy",type=Path,default=ROOT/"pfd/migration_audit.json")
    args=p.parse_args()
    cfg=json.loads(args.policy.read_text(encoding="utf-8"))
    errors=0
    for lane in cfg["lanes"]:
        try:
            print(json.dumps(audit(cfg["repository"],lane),sort_keys=True))
        except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
            errors+=1
            print(json.dumps({"pr":lane["pr"],"decision":"HOLD","error":str(exc)}))
    return int(bool(errors))

if __name__=="__main__":
    raise SystemExit(main())
