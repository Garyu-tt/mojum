"""Deterministic restart-recovery planning for supervisor task snapshots.

Storage- and transport-independent: proposes actions without mutating a queue.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from typing import Any

SCHEMA = "mojum.qa-recovery/1"
MAX_TASKS = 500
MAX_DEPENDENCIES = 4000
MAX_ID_BYTES = 128
MAX_ATTEMPTS = 1000
_STATES = {
    "DISCOVERED", "CLAIMED", "RUNNING", "RETRY", "WAITING_CI",
    "BLOCKED_PERMISSION", "BLOCKED_CONFLICT", "SUCCEEDED", "FAILED",
    "CANCELLED",
}
_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


class RecoveryError(ValueError):
    """Invalid or unsafe recovery input."""


@dataclass(frozen=True)
class RecoveryAction:
    task_id: str
    decision: str
    reason: str
    attempt: int


@dataclass(frozen=True)
class RecoveryReport:
    schema: str
    observed_at: int
    actions: tuple[RecoveryAction, ...]
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "observed_at": self.observed_at,
            "actions": [
                {"task_id": a.task_id, "decision": a.decision,
                 "reason": a.reason, "attempt": a.attempt}
                for a in self.actions
            ],
            "digest": self.digest,
        }


def _integer(value: Any, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise RecoveryError(f"{field} must be a native integer >= {minimum}")
    return value


def _identifier(value: Any, field: str) -> str:
    if type(value) is not str or not value:
        raise RecoveryError(f"{field} must be a non-empty bounded UTF-8 string")
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise RecoveryError(f"{field} is not valid UTF-8 text") from exc
    if encoded_length > MAX_ID_BYTES:
        raise RecoveryError(f"{field} must be a non-empty bounded UTF-8 string")
    if any(ord(ch) < 33 or ord(ch) == 127 for ch in value):
        raise RecoveryError(f"{field} contains whitespace or control characters")
    return value


def _digest(observed_at: int, actions: tuple[RecoveryAction, ...]) -> str:
    body = {
        "schema": SCHEMA,
        "observed_at": observed_at,
        "actions": [[a.task_id, a.decision, a.reason, a.attempt] for a in actions],
    }
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def plan_recovery(tasks: Any, *, observed_at: int) -> RecoveryReport:
    """Build a bounded deterministic recovery plan from a trusted queue snapshot.

    Task schema: task_id, state, attempt, max_attempts, lease_expires_at,
    not_before, dependencies. Times are non-negative integer logical seconds.
    A lease expires when lease_expires_at <= observed_at.
    """
    now = _integer(observed_at, "observed_at")
    if type(tasks) is not list or len(tasks) > MAX_TASKS:
        raise RecoveryError(f"tasks must be a built-in list of at most {MAX_TASKS}")
    required = {"task_id", "state", "attempt", "max_attempts",
                "lease_expires_at", "not_before", "dependencies"}
    by_id: dict[str, dict[str, Any]] = {}
    edges = 0
    for index, item in enumerate(tasks):
        if type(item) is not dict or set(item) != required:
            raise RecoveryError(f"tasks[{index}] must have the exact task schema")
        task_id = _identifier(item["task_id"], f"tasks[{index}].task_id")
        if task_id in by_id:
            raise RecoveryError(f"duplicate task_id: {task_id}")
        state = item["state"]
        if type(state) is not str or state not in _STATES:
            raise RecoveryError(f"tasks[{index}].state is unsupported")
        attempt = _integer(item["attempt"], f"tasks[{index}].attempt")
        maximum = _integer(item["max_attempts"], f"tasks[{index}].max_attempts", 1)
        if attempt > maximum or maximum > MAX_ATTEMPTS:
            raise RecoveryError(f"tasks[{index}] retry budget is invalid")
        lease = item["lease_expires_at"]
        if lease is not None:
            lease = _integer(lease, f"tasks[{index}].lease_expires_at")
        not_before = _integer(item["not_before"], f"tasks[{index}].not_before")
        deps = item["dependencies"]
        if type(deps) is not list or len(deps) > MAX_TASKS:
            raise RecoveryError(f"tasks[{index}].dependencies must be a bounded list")
        clean_deps = [_identifier(dep, f"tasks[{index}].dependencies") for dep in deps]
        if len(set(clean_deps)) != len(clean_deps) or task_id in clean_deps:
            raise RecoveryError(f"tasks[{index}] has duplicate or self dependency")
        edges += len(clean_deps)
        if edges > MAX_DEPENDENCIES:
            raise RecoveryError("dependency edge budget exceeded")
        if state in {"CLAIMED", "RUNNING"} and lease is None:
            raise RecoveryError(f"active task {task_id} has no lease expiry")
        if state in {"CLAIMED", "RUNNING"} and attempt < 1:
            raise RecoveryError(f"active task {task_id} must have at least one claim attempt")
        if state not in {"CLAIMED", "RUNNING"} and lease is not None:
            raise RecoveryError(f"inactive task {task_id} unexpectedly has a lease")
        by_id[task_id] = {
            "state": state, "attempt": attempt, "maximum": maximum,
            "lease": lease, "not_before": not_before, "dependencies": clean_deps,
        }

    for task_id, item in by_id.items():
        for dep in item["dependencies"]:
            if dep not in by_id:
                raise RecoveryError(f"task {task_id} references unknown dependency {dep}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise RecoveryError("dependency cycle detected")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dep in by_id[task_id]["dependencies"]:
            visit(dep)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in sorted(by_id):
        visit(task_id)

    actions = []
    for task_id in sorted(by_id):
        item = by_id[task_id]
        state, attempt = item["state"], item["attempt"]
        if state in _TERMINAL:
            decision, reason = "KEEP_TERMINAL", "terminal_state"
        elif state in {"CLAIMED", "RUNNING"}:
            if item["lease"] > now:
                decision, reason = "KEEP_LEASE", "lease_still_valid"
            elif attempt >= item["maximum"]:
                decision, reason = "MARK_FAILED", "lease_expired_retry_budget_exhausted"
            else:
                decision, reason = "REQUEUE", "lease_expired_retry_available"
        elif state == "RETRY" and attempt >= item["maximum"]:
            decision, reason = "MARK_FAILED", "retry_budget_exhausted"
        elif state in {"WAITING_CI", "BLOCKED_PERMISSION", "BLOCKED_CONFLICT"}:
            decision, reason = "DEFER", "external_gate_or_operator_required"
        else:
            dependency_states = [by_id[dep]["state"] for dep in item["dependencies"]]
            if any(s in {"FAILED", "CANCELLED"} for s in dependency_states):
                decision, reason = "BLOCK_DEPENDENCY", "dependency_terminal_without_success"
            elif any(s != "SUCCEEDED" for s in dependency_states):
                decision, reason = "DEFER", "dependency_incomplete"
            elif item["not_before"] > now:
                decision, reason = "DEFER", "retry_not_due"
            else:
                decision, reason = "READY", "dependencies_satisfied"
        actions.append(RecoveryAction(task_id, decision, reason, attempt))

    frozen = tuple(actions)
    return RecoveryReport(SCHEMA, now, frozen, _digest(now, frozen))
