# CPU Legion R0.1: bounded, offline multi-host capacity admission

This is a **read-only resource planner**, not an active worker deployment.
It supports the separate Spartan 300 workstream in issue #478 and does not
alter PR #477's SQLite dispatcher, workers, repositories or CI policies.

## Run

Python 3.11+ and the standard library are sufficient.

    python3 tools/cpu_legion_capacity.py --inventory examples/cpu_legion_inventory.example.json --target 4 --require-full
    python3 tools/cpu_legion_capacity.py --inventory examples/cpu_legion_inventory.example.json --target 16 --require-full
    python3 -m unittest discover -s tests -p test_cpu_legion_capacity.py -v

The example is **synthetic**, not a measurement of Mikoshi Tower, the legacy
server, or any connected node. Before using real tasks, operators must inventory
actual logical CPU capacity, available host memory, host/system reserves,
worker model memory requirements, and conservative per-node worker limits.

## Contract

Inventory schema: mojum.cpu-legion-inventory/1. Required fields:
target_workers (0..300), worker (positive cpu_millicores and memory_mib),
and a nonempty nodes list. Each node requires a unique canonical mesh-style
id, positive CPU/memory totals, nonnegative CPU/memory host reserves and
max_workers (0..300). Optional enabled is strictly boolean.

Unknown keys, duplicate JSON keys, float/bool resource numbers,
over-reservation, non-UTF-8, nonstandard NaN and files above 1 MiB are rejected.

Capacity per node is:

    min(max_workers,
        floor((cpu_millicores - reserve_cpu_millicores) / worker.cpu_millicores),
        floor((memory_mib - reserve_memory_mib) / worker.memory_mib))

Disabled nodes admit no workers. Assignment is deterministic: sort node IDs,
then fill in fair round-robin passes until the target is reached or all nodes
are full. JSON output exposes per-host usable CPU/RAM, admission ceiling,
assigned workers and planned reservations. Unplaced workers are never
silently counted as admitted.

Start with --target 4, then 16 and 32, and only raise toward 300 after
measuring actual model throughput, latency, worker health, cost and CI review.

Exit codes: 0 for valid planning; 1 for invalid inventory/read errors;
2 for a valid capacity shortfall when --require-full is set. A shortfall
still prints JSON diagnostics, while invalid input prints no JSON.

## Safety boundary

- A plan does **not** provision hosts, start/reserve/sandbox/count live
  processes, enforce quotas or perform GitHub operations.
- Input is caller-provided and unauthenticated: neither an enrollment
  proof nor a live health check, CPU benchmark or free-memory measurement.
- Millicores are accounting units, not physical core counts or cgroup
  enforcement. Hyperthreading, contention, model size and context length
  must be benchmarked independently.
- Future dispatcher integration must verify current host inventory and
  heartbeat, enforce OS/container limits, rate-limit external models,
  separate inference credentials from GitHub write credentials, and retain
  independent exact-head QA acceptance. Even admitted=300 proves only
  an inventory arithmetic plan, not 300 running agents.

No PRIVATE journals, SQLite state, or remote hosts are touched.
