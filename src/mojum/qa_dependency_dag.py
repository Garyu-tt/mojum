"""Bounded, deterministic PR dependency DAG admission for the QA supervisor.

Consumes caller-supplied metadata only. No polling, journals, scheduling,
worker execution, or merge authorization.
"""
from __future__ import annotations

from typing import Any

SCHEMA = "mojum.qa-dependency-dag/1"
MAX_NODES = 500
MAX_EDGES = 4000
_STATES = frozenset({"OPEN", "MERGED", "CLOSED"})


def evaluate_dependency_dag(
    records: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    max_nodes: int = MAX_NODES,
    max_edges: int = MAX_EDGES,
) -> dict[str, Any]:
    """Return a stable dependency-first plan or raise ValueError.

    Only exact built-in list/tuple/dict/str containers and values are admitted.
    Each record has exactly id, state and depends_on. An OPEN PR is READY only
    when every direct dependency is MERGED. CLOSED dependencies remain blocking.
    """
    if type(records) not in (list, tuple):
        raise ValueError("records must be a native list or tuple")
    if type(max_nodes) is not int or not 1 <= max_nodes <= MAX_NODES:
        raise ValueError("max_nodes must be an integer in [1, 500]")
    if type(max_edges) is not int or not 0 <= max_edges <= MAX_EDGES:
        raise ValueError("max_edges must be an integer in [0, 4000]")
    if len(records) > max_nodes:
        raise ValueError("node budget exceeded")

    states: dict[str, str] = {}
    dependencies: dict[str, tuple[str, ...]] = {}
    edge_count = 0

    for record in records:
        if type(record) is not dict:
            raise ValueError("each record must be a native dict")
        keys = tuple(record.keys())
        if any(type(key) is not str for key in keys):
            raise ValueError("record keys must be native strings")
        if set(keys) != {"id", "state", "depends_on"}:
            raise ValueError("record must contain exactly id, state, depends_on")

        node_id, state, raw_deps = record["id"], record["state"], record["depends_on"]
        if type(node_id) is not str or not node_id or len(node_id) > 128:
            raise ValueError("invalid PR id")
        if node_id in states:
            raise ValueError("duplicate PR id")
        if type(state) is not str or state not in _STATES:
            raise ValueError("invalid PR state")
        if type(raw_deps) not in (list, tuple):
            raise ValueError("depends_on must be a native list or tuple")
        if len(raw_deps) > max_edges:
            raise ValueError("edge budget exceeded")

        deps: list[str] = []
        seen: set[str] = set()
        for dep in raw_deps:
            if type(dep) is not str or not dep or len(dep) > 128:
                raise ValueError("invalid dependency id")
            if dep == node_id:
                raise ValueError("self dependency")
            if dep in seen:
                raise ValueError("duplicate dependency")
            seen.add(dep)
            deps.append(dep)
        edge_count += len(deps)
        if edge_count > max_edges:
            raise ValueError("edge budget exceeded")
        states[node_id] = state
        dependencies[node_id] = tuple(deps)

    node_ids = set(states)
    for node_id, deps in dependencies.items():
        if any(dep not in node_ids for dep in deps):
            raise ValueError(f"dangling dependency for {node_id}")

    # Kahn's algorithm with deterministic lexicographic frontier ordering.
    dependents: dict[str, list[str]] = {node: [] for node in states}
    remaining: dict[str, int] = {}
    for node, deps in dependencies.items():
        remaining[node] = len(deps)
        for dep in deps:
            dependents[dep].append(node)
    frontier = sorted(node for node, count in remaining.items() if count == 0)
    order: list[str] = []
    while frontier:
        node = frontier.pop(0)
        order.append(node)
        for child in sorted(dependents[node]):
            remaining[child] -= 1
            if remaining[child] == 0:
                frontier.append(child)
        frontier.sort()
    if len(order) != len(states):
        raise ValueError("dependency cycle detected")

    decisions: dict[str, dict[str, Any]] = {}
    for node in order:
        state, deps = states[node], dependencies[node]
        if state == "MERGED":
            status, reason, waiting = "TERMINAL", "already_merged", []
        elif state == "CLOSED":
            status, reason, waiting = "TERMINAL", "closed", []
        else:
            waiting = sorted(dep for dep in deps if states[dep] != "MERGED")
            if waiting:
                status, reason = "BLOCKED", "dependencies_not_merged"
            else:
                status, reason = "READY", "all_dependencies_merged"
        decisions[node] = {"status": status, "reason": reason, "waiting_on": waiting}

    return {
        "schema": SCHEMA,
        "node_count": len(states),
        "edge_count": edge_count,
        "topological_order": order,
        "decisions": decisions,
    }
