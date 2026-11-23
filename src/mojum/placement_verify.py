"""Independent, offline safety audit of advisory stateless-job placements.

This verifies a proposed placement, including one received from a different
planner, without executing jobs or granting trust. The caller must supply
independently vetted node budgets and remote admission decisions. It does not
establish authentication, capacity measurements, leases, or scheduling fairness.
"""

from .placement import (
    MAX_JOBS, MAX_NODES, Assignment, NodeBudget, PlacementError,
    PlacementPlan, StatelessJob, _check_id, _snapshot_records,
)


def verify_placement(candidate, jobs, nodes, *, local_node_id,
                     trusted_remote_ids=frozenset()):
    """Return True only when every requested job is safely accounted for.

    A job may be assigned exactly once or explicitly deferred. All assignments
    must fit the caller-declared *free* budgets, capabilities and admitted
    online nodes. Safe but suboptimal plans (including deliberate deferrals)
    remain valid; this is a safety verifier, not an optimality certificate.
    """
    _check_id(local_node_id)
    if (type(jobs) not in (tuple, list) or len(jobs) > MAX_JOBS
            or type(nodes) not in (tuple, list)
            or not 1 <= len(nodes) <= MAX_NODES
            or type(trusted_remote_ids) is not frozenset
            or len(trusted_remote_ids) >= MAX_NODES):
        raise PlacementError("invalid verifier inputs")
    if (any(type(job) is not StatelessJob for job in jobs)
            or any(type(node) is not NodeBudget for node in nodes)
            or any(type(node_id) is not str for node_id in trusted_remote_ids)):
        raise PlacementError("invalid verifier records")
    for node_id in trusted_remote_ids:
        _check_id(node_id)

    jobs, nodes = _snapshot_records(jobs, nodes)
    by_job = {job.job_id: job for job in jobs}
    by_node = {node.node_id: node for node in nodes}
    if len(by_job) != len(jobs) or len(by_node) != len(nodes):
        raise PlacementError("duplicate job or node ID")
    if local_node_id not in by_node or not by_node[local_node_id].online:
        raise PlacementError("local runtime must be available")
    if local_node_id in trusted_remote_ids or not trusted_remote_ids <= by_node.keys():
        raise PlacementError("unknown or local ID in trusted remotes")

    if (type(candidate) is not PlacementPlan
            or type(candidate.assignments) is not tuple
            or type(candidate.deferred) is not tuple
            or len(candidate.assignments) + len(candidate.deferred) != len(jobs)):
        raise PlacementError("invalid or incomplete placement plan")

    remaining = {node.node_id: [node.cpu_slots, node.memory_mib]
                 for node in nodes}
    accounted = set()
    for assignment in candidate.assignments:
        if type(assignment) is not Assignment:
            raise PlacementError("invalid assignment record")
        _check_id(assignment.job_id)
        _check_id(assignment.node_id)
        if assignment.job_id not in by_job or assignment.job_id in accounted:
            raise PlacementError("unknown or repeated job")
        node = by_node.get(assignment.node_id)
        if node is None or not node.online:
            raise PlacementError("unknown or offline placement node")
        if node.node_id != local_node_id and node.node_id not in trusted_remote_ids:
            raise PlacementError("assignment to untrusted remote")
        job = by_job[assignment.job_id]
        if not set(job.required_capabilities) <= set(node.capabilities):
            raise PlacementError("missing required capability")
        remaining[node.node_id][0] -= job.cpu_slots
        remaining[node.node_id][1] -= job.memory_mib
        if min(remaining[node.node_id]) < 0:
            raise PlacementError("placement exceeds free node budget")
        accounted.add(job.job_id)

    for job_id in candidate.deferred:
        _check_id(job_id)
        if job_id not in by_job or job_id in accounted:
            raise PlacementError("unknown or repeated deferred job")
        accounted.add(job_id)
    if accounted != by_job.keys():
        raise PlacementError("placement omits a requested job")
    return True
