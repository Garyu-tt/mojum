"""Deterministic, CPU-only *advisory* placement of optional stateless jobs.

No peer discovery, trust verification, remote execution, network access,
leases, resource measurement, or migration of an organism's mutable state.
The caller must supply free budgets *after* reserving the local organism.
"""
from dataclasses import dataclass

from .mesh_contract import Offer, PROTOCOL, _NODE_ID, _validated

MAX_NODES = 64
MAX_JOBS = 256
MAX_CPU_SLOTS = 4096
MAX_MEMORY_MIB = 4_194_304


class PlacementError(ValueError):
    """An unsafe or ambiguous advisory placement request."""


def _check_id(value):
    if (type(value) is not str or not 1 <= len(value) <= 128
            or _NODE_ID.fullmatch(value) is None):
        raise PlacementError("noncanonical identifier")


def _check_capabilities(capabilities):
    try:
        _validated(Offer(PROTOCOL, "fixture", capabilities))
    except ValueError as exc:
        raise PlacementError("invalid capability set") from exc


def _budget_number(value, max_value, *, allow_zero):
    return type(value) is int and (0 if allow_zero else 1) <= value <= max_value


@dataclass(frozen=True)
class NodeBudget:
    """Caller-declared *free* resources, excluding reserved core state."""
    node_id: str
    cpu_slots: int
    memory_mib: int
    capabilities: tuple[str, ...]
    online: bool = True

    def __post_init__(self):
        _check_id(self.node_id)
        _check_capabilities(self.capabilities)
        if (not _budget_number(self.cpu_slots, MAX_CPU_SLOTS, allow_zero=True)
                or not _budget_number(self.memory_mib, MAX_MEMORY_MIB, allow_zero=True)
                or type(self.online) is not bool):
            raise PlacementError("invalid node budget")


@dataclass(frozen=True)
class StatelessJob:
    """No organism brain/body or authoritative state can be scheduled here."""
    job_id: str
    cpu_slots: int
    memory_mib: int
    required_capabilities: tuple[str, ...] = ()
    priority: int = 0
    stateless: bool = True

    def __post_init__(self):
        _check_id(self.job_id)
        _check_capabilities(self.required_capabilities)
        if (not _budget_number(self.cpu_slots, MAX_CPU_SLOTS, allow_zero=False)
                or not _budget_number(self.memory_mib, MAX_MEMORY_MIB, allow_zero=False)
                or type(self.priority) is not int or not 0 <= self.priority <= 9
                or self.stateless is not True):
            raise PlacementError("unsupported job budget or stateful job")


@dataclass(frozen=True)
class Assignment:
    job_id: str
    node_id: str


@dataclass(frozen=True)
class PlacementPlan:
    assignments: tuple[Assignment, ...]
    deferred: tuple[str, ...]


def plan(jobs, nodes, *, local_node_id, trusted_remote_ids=frozenset()):
    """Return a side-effect-free placement suggestion with local preference.

    The local node is always the first candidate if it can fit a job. Remotes
    are included only when explicitly named by a separately trusted caller.
    The names alone are NOT proof of authentication, enrollment or online status.
    A deferred job is not dropped or executed on an untrusted/oversubscribed node.
    """
    _check_id(local_node_id)
    if (type(jobs) not in (tuple, list) or len(jobs) > MAX_JOBS
            or type(nodes) not in (tuple, list) or not 1 <= len(nodes) <= MAX_NODES
            or type(trusted_remote_ids) is not frozenset):
        raise PlacementError("invalid planner input")
    if (any(type(j) is not StatelessJob for j in jobs)
            or any(type(n) is not NodeBudget for n in nodes)
            or any(type(x) is not str for x in trusted_remote_ids)):
        raise PlacementError("invalid planner records")
    all_nodes = {n.node_id: n for n in nodes}
    all_jobs = {j.job_id: j for j in jobs}
    if len(all_nodes) != len(nodes) or len(all_jobs) != len(jobs):
        raise PlacementError("duplicate node or job ID")
    if local_node_id not in all_nodes or not all_nodes[local_node_id].online:
        raise PlacementError("local runtime must be available")
    if local_node_id in trusted_remote_ids or not trusted_remote_ids <= set(all_nodes):
        raise PlacementError("unknown or local ID in trusted remotes")

    available = {n.node_id: [n.cpu_slots, n.memory_mib] for n in nodes}
    eligible = [all_nodes[local_node_id]] + sorted(
        (all_nodes[i] for i in trusted_remote_ids if all_nodes[i].online),
        key=lambda x: x.node_id,
    )
    assignments = []
    deferred = []
    for job in sorted(jobs, key=lambda x: (-x.priority, x.job_id)):
        choice = next((n for n in eligible
                       if set(job.required_capabilities) <= set(n.capabilities)
                       and available[n.node_id][0] >= job.cpu_slots
                       and available[n.node_id][1] >= job.memory_mib), None)
        if choice is None:
            deferred.append(job.job_id)
            continue
        available[choice.node_id][0] -= job.cpu_slots
        available[choice.node_id][1] -= job.memory_mib
        assignments.append(Assignment(job.job_id, choice.node_id))
    return PlacementPlan(tuple(assignments), tuple(deferred))
