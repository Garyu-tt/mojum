"""Deterministic selection of runnable QA tasks without head-of-line blocking.

This module is a pure scheduling policy. It does not claim leases, access GitHub,
or mutate the task snapshots supplied by its caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


MAX_TASKS = 10_000
MAX_BATCH = 256
MAX_ID_LENGTH = 128

RUNNABLE_STATES = frozenset({"DISCOVERED", "RETRY"})
BLOCKED_STATES = frozenset({
    "WAITING_CI",
    "BLOCKED_PERMISSION",
    "BLOCKED_CONFLICT",
    "ESCALATED",
})
ACTIVE_STATES = frozenset({"CLAIMED", "REVIEWING", "TESTING", "FIXING", "RETESTING"})
TERMINAL_STATES = frozenset({"READY", "MERGED", "CLOSED", "CANCELLED"})
ALL_STATES = RUNNABLE_STATES | BLOCKED_STATES | ACTIVE_STATES | TERMINAL_STATES


class SelectionError(ValueError):
    """Raised when a task snapshot violates the bounded input contract."""


@dataclass(frozen=True, slots=True)
class Selection:
    """Detached scheduling result; task IDs only, never caller-owned records."""

    selected: tuple[str, ...]
    deferred: tuple[tuple[str, str], ...]
    ignored: tuple[tuple[str, str], ...]


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SelectionError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _identifier(value: Any, name: str = "task_id") -> str:
    if type(value) is not str or not value or len(value) > MAX_ID_LENGTH:
        raise SelectionError(f"{name} must be a non-empty string of at most {MAX_ID_LENGTH} characters")
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in value):
        raise SelectionError(f"{name} must contain printable non-space ASCII only")
    return value


def select_runnable_tasks(
    tasks: Sequence[Mapping[str, Any]],
    *,
    now_tick: int,
    limit: int = 16,
) -> Selection:
    """Return runnable IDs in stable priority/FIFO order.

    Required fields per exact dict: task_id, state, priority, sequence.
    Optional fields: not_before (logical tick, default 0), dependencies
    (list of task IDs, each of which must be MERGED).

    Only DISCOVERED and RETRY are candidates. Waiting CI, permission/conflict
    blocks, escalation, active work and terminal states are never selected.
    Missing or non-merged dependencies defer a candidate rather than stopping
    selection of unrelated work. Input order never affects the result.
    """
    _integer(now_tick, "now_tick", 0, 2**63 - 1)
    _integer(limit, "limit", 1, MAX_BATCH)
    if type(tasks) is not list:
        raise SelectionError("tasks must be an exact list")
    if len(tasks) > MAX_TASKS:
        raise SelectionError(f"tasks exceeds maximum of {MAX_TASKS}")

    normalized: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(tasks):
        if type(item) is not dict:
            raise SelectionError(f"tasks[{index}] must be an exact dict")
        allowed = {"task_id", "state", "priority", "sequence", "not_before", "dependencies"}
        if set(item) - allowed or not {"task_id", "state", "priority", "sequence"} <= set(item):
            raise SelectionError(f"tasks[{index}] has missing or unknown fields")
        task_id = _identifier(item["task_id"])
        if task_id in normalized:
            raise SelectionError(f"duplicate task_id: {task_id}")
        state = item["state"]
        if type(state) is not str or state not in ALL_STATES:
            raise SelectionError(f"tasks[{index}].state is unsupported")
        priority = _integer(item["priority"], f"tasks[{index}].priority", 0, 1_000_000)
        sequence = _integer(item["sequence"], f"tasks[{index}].sequence", 0, 2**63 - 1)
        not_before = _integer(item.get("not_before", 0), f"tasks[{index}].not_before", 0, 2**63 - 1)
        dependencies = item.get("dependencies", [])
        if type(dependencies) is not list or len(dependencies) > 256:
            raise SelectionError(f"tasks[{index}].dependencies must be a bounded exact list")
        deps = tuple(_identifier(dep, f"tasks[{index}].dependencies[]") for dep in dependencies)
        if len(set(deps)) != len(deps) or task_id in deps:
            raise SelectionError(f"tasks[{index}].dependencies contains duplicates or self-reference")
        normalized[task_id] = {
            "state": state, "priority": priority, "sequence": sequence,
            "not_before": not_before, "dependencies": deps,
        }

    candidates: list[tuple[int, int, str]] = []
    deferred: list[tuple[str, str]] = []
    ignored: list[tuple[str, str]] = []
    for task_id, task in normalized.items():
        state = task["state"]
        if state in BLOCKED_STATES:
            deferred.append((task_id, state))
            continue
        if state in ACTIVE_STATES or state in TERMINAL_STATES:
            ignored.append((task_id, state))
            continue
        if task["not_before"] > now_tick:
            deferred.append((task_id, "NOT_DUE"))
            continue
        unmet = [dep for dep in task["dependencies"]
                 if dep not in normalized or normalized[dep]["state"] != "MERGED"]
        if unmet:
            deferred.append((task_id, "DEPENDENCY_NOT_MERGED"))
            continue
        candidates.append((-task["priority"], task["sequence"], task_id))

    candidates.sort()
    selected = tuple(task_id for _, _, task_id in candidates[:limit])
    for _, _, task_id in candidates[limit:]:
        deferred.append((task_id, "BATCH_LIMIT"))

    return Selection(
        selected=selected,
        deferred=tuple(sorted(deferred)),
        ignored=tuple(sorted(ignored)),
    )
