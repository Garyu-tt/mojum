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
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise WorkflowEvidenceError(f"{field} must be a positive native integer")
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
    if type(expected_head_sha) is not str or _SHA.fullmatch(expected_head_sha) is None:
        raise WorkflowEvidenceError("expected_head_sha must be 40 lowercase hexadecimal characters")
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
    seen_runs: set[int] = set()
    stale_count = 0
    for index, raw in enumerate(runs):
        item = _fields(raw, index)
        run_id = _integer(item["run_id"], f"runs[{index}].run_id")
        attempt = _integer(item["run_attempt"], f"runs[{index}].run_attempt")
        workflow_id = _integer(item["workflow_id"], f"runs[{index}].workflow_id")
        name = _string(item["name"], f"runs[{index}].name")
        head = item["head_sha"]
        if type(head) is not str or _SHA.fullmatch(head) is None:
            raise WorkflowEvidenceError(f"runs[{index}].head_sha is malformed")
        if _string(item["event"], f"runs[{index}].event", 32) != "pull_request":
            raise WorkflowEvidenceError(f"runs[{index}] is not a pull_request run")
        status = _string(item["status"], f"runs[{index}].status", 32)
        conclusion = item["conclusion"]
        if status not in _PENDING | {"completed"}:
            raise WorkflowEvidenceError(f"runs[{index}].status is unsupported")
        if status == "completed":
            if type(conclusion) is not str or conclusion not in _FAILURES | {"success"}:
                raise WorkflowEvidenceError(f"runs[{index}].conclusion is unsupported")
        elif conclusion is not None:
            raise WorkflowEvidenceError(f"runs[{index}] pending run must have null conclusion")
        if run_id in seen_runs:
            raise WorkflowEvidenceError("duplicate run_id")
        seen_runs.add(run_id)
        if workflow_id not in required:
            continue
        if head != expected_head_sha:
            stale_count += 1
            continue
        candidate = {"run_id": run_id, "run_attempt": attempt, "name": name,
                     "head_sha": head, "status": status, "conclusion": conclusion}
        previous = latest.get(workflow_id)
        if previous is None or (attempt, run_id) > (previous["run_attempt"], previous["run_id"]):
            latest[workflow_id] = candidate
        elif (attempt, run_id) == (previous["run_attempt"], previous["run_id"]):
            raise WorkflowEvidenceError("ambiguous duplicate workflow attempt")

    results = []
    for workflow_id in sorted(required):
        item = latest.get(workflow_id)
        if item is None:
            results.append(WorkflowResult(workflow_id, "", "MISSING", None, None, None))
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
