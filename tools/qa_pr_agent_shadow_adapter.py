"""Shadow adapter for the canonical Mojum QA supervisor, not a queue owner."""
import re
from qa_pr_agent_batch import REPO, eligible

SHA = re.compile(r"[0-9a-f]{40}")
KIND = "pr_agent_advisory_v1"


def validate_payload(payload):
    if type(payload) is not dict or set(payload) != {"pr", "head", "base"}:
        raise ValueError("expected exact PR/head/base identity")
    pr = payload["pr"]
    if type(pr) is not int or not 1 <= pr <= 2**63 - 1:
        raise ValueError("invalid PR number")
    for key in ("head", "base"):
        if type(payload[key]) is not str or not SHA.fullmatch(payload[key]):
            raise ValueError("invalid revision")
    return dict(payload)


def job_spec(payload):
    p = validate_payload(payload)
    return {"kind": KIND, "idempotency_key": f"pr-agent:{p['pr']}:{p['head']}:{p['base']}",
            "payload": p, "max_attempts": 2}


def shadow_dispatch(lease, snapshot, current_base, now):
    """Check a supervisor-provided lease. Never claims/completes or returns its token.

    Caller must recheck the actual lease in the canonical queue before dispatch
    and after execution; this function is not an authorization boundary.
    """
    if lease.job.kind != KIND or lease.job.status != "leased":
        raise ValueError("not an advisory lease")
    p = validate_payload(lease.job.payload)
    if lease.expires_at <= now:
        return {"status": "LEASE_EXPIRED", **p}
    if not eligible(snapshot, p["head"]) or snapshot.get("number") != p["pr"]:
        return {"status": "STALE_HEAD_OR_INELIGIBLE", **p}
    if current_base != p["base"] or snapshot["base"].get("sha") != p["base"]:
        return {"status": "STALE_BASE", **p}
    return {"status": "SHADOW_ELIGIBLE", **p, "repository": REPO,
            "pair": f"{p['pr']}:{p['head']}", "merge_authorized": False}
