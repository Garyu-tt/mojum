"""Fail-closed admission of GitHub Actions evidence for one exact PR head."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Sequence

MAX_RUNS = 500
MAX_REQUIRED_WORKFLOWS = 64
_ALLOWED_FIELDS = frozenset({"run_id", "run_attempt", "workflow_id", "name", "head_sha", "event", "status", "conclusion"})
_PENDING = frozenset({"queued", "in_progress", "pending", "waiting", "requested"})
_FAILURES = frozenset({"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"})
_SHA = re.compile(r"[0-9a-f]{40}")

class WorkflowEvidenceError(ValueError):
    """Invalid or ambiguous workflow evidence."""

@dataclass(frozen=True, slots=True)
class WorkflowResult:
    workflow_id: int
    name: str
    state: str
    run_id: int | None
    run_attempt: int | None
    head_sha: str | None

@dataclass(frozen=True, slots=True)
class WorkflowEvidence:
    schema: str
    head_sha: str
    state: str
    results: tuple[WorkflowResult, ...]
    stale_run_count: int
    digest: str

def _integer(value: Any, field: str) -> int:
    if type(value) is not int:
        raise WorkflowEvidenceError(f"{field} must be a positive native integer")
    if value.bit_length() > 63 or value < 1:
        raise WorkflowEvidenceError(f"{field} must be a positive signed-64-bit integer")
    return value

def _string(value: Any, field: str, maximum: int = 160) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise WorkflowEvidenceError(f"{field} must be a bounded non-empty native string")
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in value):
        raise WorkflowEvidenceError(f"{field} must be printable ASCII")
    return value

def _fields(raw: Any, index: int) -> dict[str, Any]:
    if type(raw) is not dict or len(raw) != len(_ALLOWED_FIELDS):
        raise WorkflowEvidenceError(f"runs[{index}] must be an exact dict with the documented fields")
    result = {}
    for key, value in dict.items(raw):
        if type(key) is not str or not key or len(key) > 24:
            raise WorkflowEvidenceError(f"runs[{index}] has a hostile field name")
        result[key] = value
    if frozenset(result) != _ALLOWED_FIELDS:
        raise WorkflowEvidenceError(f"runs[{index}] has missing or unknown fields")
    return result

def evaluate_workflow_evidence(
    runs: Sequence[dict[str, Any]], *, expected_head_sha: str,
    required_workflow_ids: Sequence[int],
) -> WorkflowEvidence:
    """Require the latest attempt of every required workflow to pass on this exact HEAD.

    Newer pending attempts supersede earlier successful attempts. Stale-head
    runs never satisfy a requirement. This function does not fetch or
    authenticate snapshots and does not authorize merges.
    """
    if type(expected_head_sha) is not str or len(expected_head_sha) != 40 or _SHA.fullmatch(expected_head_sha) is None:
        raise WorkflowEvidenceError("expected_head_sha must be exactly 40 lowercase hexadecimal characters")
    if type(runs) is not list or len(runs) > MAX_RUNS:
        raise WorkflowEvidenceError(f"runs must be an exact list of at most {MAX_RUNS} records")
    if type(required_workflow_ids) is not list or not 1 <= len(required_workflow_ids) <= MAX_REQUIRED_WORKFLOWS:
        raise WorkflowEvidenceError("required_workflow_ids must be an exact non-empty bounded list")
    required = []
    for value in required_workflow_ids:
        workflow_id = _integer(value, "required workflow_id")
        if workflow_id in required:
            raise WorkflowEvidenceError("duplicate required workflow_id")
        required.append(workflow_id)

    latest: dict[int, dict[str, Any]] = {}
    seen_attempts: set[tuple[int, int]] = set()
    run_identities: dict[int, tuple[int, str, str, str]] = {}
    stale_count = 0
    for index, raw in enumerate(runs):
        item = _fields(raw, index)
        run_id = _integer(item["run_id"], f"runs[{index}].run_id")
        attempt = _integer(item["run_attempt"], f"runs[{index}].run_attempt")
        workflow_id = _integer(item["workflow_id"], f"runs[{index}].workflow_id")
        name = _string(item["name"], f"runs[{index}].name")
        head = item["head_sha"]
        if type(head) is not str or len(head) != 40 or _SHA.fullmatch(head) is None:
            raise WorkflowEvidenceError(f"runs[{index}].head_sha is malformed")
        if _string(item["event"], f"runs[{index}].event", 32) != "pull_request":
            raise WorkflowEvidenceError(f"runs[{index}] is not a pull_request run")
        status = _string(item["status"], f"runs[{index}].status", 32)
        conclusion = item["conclusion"]
        if status not in _PENDING | {"completed"}:
            raise WorkflowEvidenceError(f"runs[{index}].status is unsupported")
        if status == "completed":
            # Bound caller-controlled conclusion text before hashing it in set membership.
            if type(conclusion) is not str or not conclusion or len(conclusion) > 32:
                raise WorkflowEvidenceError(f"runs[{index}].conclusion is unsupported")
            if conclusion not in _FAILURES | {"success", "neutral", "skipped"}:
                raise WorkflowEvidenceError(f"runs[{index}].conclusion is unsupported")
        elif conclusion is not None:
            raise WorkflowEvidenceError(f"runs[{index}].conclusion must be null for a pending run")
        attempt_key = (run_id, attempt)
        if attempt_key in seen_attempts:
            raise WorkflowEvidenceError("duplicate workflow run attempt")
        seen_attempts.add(attempt_key)
        identity = (workflow_id, name, head, item["event"])
        previous_identity = run_identities.get(run_id)
        if previous_identity is not None and previous_identity != identity:
            raise WorkflowEvidenceError("workflow run identity changed across attempts")
        run_identities[run_id] = identity
        if workflow_id not in required:
            continue
        if head != expected_head_sha:
            stale_count += 1
        # Select the newest run before evaluating its HEAD, so a stale latest
        # run cannot be ignored in favor of an older exact-head success.
        candidate = {"run_id": run_id, "run_attempt": attempt, "name": name,
                     "head_sha": head, "status": status, "conclusion": conclusion}
        previous = latest.get(workflow_id)
        if previous is None or (run_id, attempt) > (previous["run_id"], previous["run_attempt"]):
            latest[workflow_id] = candidate
        elif (run_id, attempt) == (previous["run_id"], previous["run_attempt"]):
            raise WorkflowEvidenceError("ambiguous duplicate workflow attempt")

    results = []
    for workflow_id in sorted(required):
        item = latest.get(workflow_id)
        if item is None:
            results.append(WorkflowResult(workflow_id, "", "MISSING", None, None, None))
        elif item["head_sha"] != expected_head_sha:
            results.append(WorkflowResult(workflow_id, item["name"], "STALE", item["run_id"], item["run_attempt"], item["head_sha"]))
        elif item["status"] != "completed":
            results.append(WorkflowResult(workflow_id, item["name"], "PENDING", item["run_id"], item["run_attempt"], item["head_sha"]))
        elif item["conclusion"] != "success":
            results.append(WorkflowResult(workflow_id, item["name"], "FAILURE", item["run_id"], item["run_attempt"], item["head_sha"]))
        else:
            results.append(WorkflowResult(workflow_id, item["name"], "PASS", item["run_id"], item["run_attempt"], item["head_sha"]))
    aggregate = "PASS" if all(r.state == "PASS" for r in results) else "HOLD"
    canonical = {"schema": "mojum.qa.workflow-evidence/1", "head_sha": expected_head_sha,
                 "state": aggregate, "stale_run_count": stale_count,
                 "results": [{"workflow_id": r.workflow_id, "name": r.name, "state": r.state,
                              "run_id": r.run_id, "run_attempt": r.run_attempt, "head_sha": r.head_sha}
                             for r in results]}
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    digest = hashlib.sha256(b"mojum.qa.workflow-evidence/1\0" + payload).hexdigest()
    return WorkflowEvidence(canonical["schema"], expected_head_sha, aggregate, tuple(results), stale_count, digest)
