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
MAX_FIELD_NAME_LENGTH = 16
MAX_TOTAL_DEPENDENCIES = 100_000

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
_ALLOWED_FIELDS = frozenset({
    "task_id", "state", "priority", "sequence", "not_before", "dependencies",
})
_REQUIRED_FIELDS = frozenset({"task_id", "state", "priority", "sequence"})


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


def _native_fields(item: dict[str, Any], index: int) -> dict[str, Any]:
    """Copy a tiny exact-dict record only after hostile keys are excluded.

    Calling set(item), item.get(), or item[literal] before this admission is
    unsafe: a str subclass used as a key can execute custom hash/equality code.
    """
    if len(item) > len(_ALLOWED_FIELDS):
        raise SelectionError(f"tasks[{index}] has too many fields")
    fields: dict[str, Any] = {}
    for key, value in dict.items(item):
        if type(key) is not str or not key or len(key) > MAX_FIELD_NAME_LENGTH:
            raise SelectionError(f"tasks[{index}] contains a non-native or oversized field name")
        fields[key] = value
    keys = frozenset(fields)
    if keys - _ALLOWED_FIELDS or not _REQUIRED_FIELDS <= keys:
        raise SelectionError(f"tasks[{index}] has missing or unknown fields")
    return fields


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
    total_dependencies = 0
    for index, item in enumerate(tasks):
        if type(item) is not dict:
            raise SelectionError(f"tasks[{index}] must be an exact dict")
        fields = _native_fields(item, index)
        task_id = _identifier(fields["task_id"])
        if task_id in normalized:
            raise SelectionError("duplicate task_id")
        state = fields["state"]
        if type(state) is not str or len(state) > 32 or state not in ALL_STATES:
            raise SelectionError(f"tasks[{index}].state is unsupported")
        priority = _integer(fields["priority"], f"tasks[{index}].priority", 0, 1_000_000)
        sequence = _integer(fields["sequence"], f"tasks[{index}].sequence", 0, 2**63 - 1)
        not_before = _integer(fields.get("not_before", 0), f"tasks[{index}].not_before", 0, 2**63 - 1)
        dependencies = fields.get("dependencies", [])
        if type(dependencies) is not list or len(dependencies) > 256:
            raise SelectionError(f"tasks[{index}].dependencies must be a bounded exact list")
        total_dependencies += len(dependencies)
        if total_dependencies > MAX_TOTAL_DEPENDENCIES:
            raise SelectionError("aggregate dependency budget exceeded")
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
