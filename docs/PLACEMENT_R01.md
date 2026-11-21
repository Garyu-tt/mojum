# Optional stateless placement R0.1 — CPU-only fixture

`mojum.placement` implements a **pure advisory plan**, not actual scheduling, remote execution, load measurement, or a distributed authority. The local organism runs independently. All resource quantities are free *surplus* budgets after reserving the organism's own CPU/RAM, heartbeat and memory. No device discovery or network is performed.

- `NodeBudget(node_id, cpu_slots, memory_mib, capabilities, online=True)` describes a caller-supplied snapshot. It is not proof of available resources, verified liveness, or a security principal.
- `StatelessJob(job_id, cpu_slots, memory_mib, required_capabilities=(), priority=0, stateless=True)` is restricted to independent optional computation. Mutable brain, body, Noosphere authority, physics checkpoint, or organism identity must not be migrated through this API.
- `plan(jobs, nodes, local_node_id=..., trusted_remote_ids=frozenset())` is deterministic: descending explicit priority, ascending job ID, then prefer local host if it fits; otherwise use an admitted remote in lexical node-ID order. Remaining jobs are **deferred**, not silently dropped.
- Remote IDs must be **explicitly selected by a separately trusted enrollment/attestation layer** and supplied as a frozen set. This planner cannot authenticate them. Callers must validate permissions and current live budgets again before launching work. No automatic LAN trust or silent remote fallback.
- Duplicate IDs, malformed mesh capability tokens, non-integer budgets, zero-resource jobs, over-budget inputs and stateful jobs fail closed.

## Example

```python
from mojum.placement import NodeBudget, StatelessJob, plan

nodes = [
    NodeBudget("phone-1", 1, 128, ("cpu",)),
    NodeBudget("laptop-1", 4, 4096, ("cpu", "physics/fixture")),
]
jobs = [StatelessJob("render-1", 2, 256, ("cpu",))]
plan(jobs, nodes, local_node_id="phone-1")  # deferred; remote has no trust grant
plan(jobs, nodes, local_node_id="phone-1",
     trusted_remote_ids=frozenset({"laptop-1"}))  # advisory laptop assignment
```

To test offline: `PYTHONPATH=src python -m unittest discover -s tests -p test_placement.py -v`.

### Not implemented

Neither the `trusted_remote_ids` parameter nor `online=True` verifies anything; a caller that misrepresents these values may cause unsafe decisions. There are no leases, authorization checks, resource reservations, cross-node commit protocol, persistence, consensus, checkpoint replication, secure channels, actual processes, or failure recovery. This may assist future work on #12, but **does not satisfy Mesh failover acceptance criteria**.
