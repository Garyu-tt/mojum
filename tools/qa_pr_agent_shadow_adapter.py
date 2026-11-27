"""Shadow adapter for the canonical Mojum QA supervisor, not a queue owner."""
import re
import hashlib
import json
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


def advisory_receipt(lease, snapshot, current_base, now, result, run_id):
    """Return bounded untrusted diagnostic evidence, never acceptance evidence.

    Caller must authenticate run provenance and recheck the live lease.
    Digest is integrity metadata, not a signature.
    """
    dispatch = shadow_dispatch(lease, snapshot, current_base, now)
    if dispatch["status"] != "SHADOW_ELIGIBLE":
        return dispatch
    if type(run_id) is not int or not 1 <= run_id <= 2**63 - 1:
        raise ValueError("invalid run ID")
    if type(result) is not dict or set(result) != {"review", "coverage", "usage"}:
        raise ValueError("invalid review envelope")
    review, coverage, usage = result["review"], result["coverage"], result["usage"]
    if type(review) is not dict or not review or type(coverage) is not dict or type(usage) is not dict:
        raise ValueError("invalid review evidence")
    if set(coverage) != {"files_not_reviewed", "files_partially_reviewed"}:
        raise ValueError("missing coverage")
    for files in coverage.values():
        if type(files) is not list or len(files) > 300 or any(type(f) is not str or not f or len(f) > 1024 for f in files):
            raise ValueError("invalid coverage")
    raw = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    if len(raw) > 65536:
        raise ValueError("review too large")
    return {"schema": "mojum.pr-agent-advisory/1", **validate_payload(lease.job.payload),
            "job_id": lease.job.id, "run_id": run_id,
            "status": "ADVISORY_INCOMPLETE" if any(coverage.values()) else "ADVISORY_UNVERIFIED",
            "review_sha256": hashlib.sha256(raw).hexdigest(),
            "result": json.loads(raw), "merge_authorized": False}
