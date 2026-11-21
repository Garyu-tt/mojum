# CPU Legion: read-only host capacity probe (R0.1)

Related to #478. Run this **on each candidate Linux host** before enrolling it
in the Spartan worker fleet. It does not need an API key, GitHub token, network
access or Mojum runtime; Python 3.11+ stdlib suffices:

~~~sh
python3 tools/spartan_host_capacity.py \
  --worker-cpu-milli 1000 --worker-mem-mib 768 \
  --reserve-cpu-milli 2000 --reserve-mem-mib 1024 --max-workers 300
~~~

The single JSON result includes "planned_slot_ceiling", "cpu_limited_slots",
"memory_limited_slots", detected CPU affinity, and effective CPU/memory caps.
On failure it returns exit 2 and JSON "status": "unavailable" instead of
silently treating a missing cgroup v2 as unlimited resources. Run with ordinary
user permissions; no privileged host changes are performed.

The result is a **conservative planning ceiling at inspection time**, not an
admission control mechanism, deployment action, proof of active workers, or
performance benchmark. A real executor must separately enforce process,
CPU, memory, timeout, network, file-system and API budgets. It must also
measure active model calls and account for worker idle versus test bursts.

## Resource model

- CPU: current process affinity (already includes effective cpuset restrictions)
  intersected with every visible cgroup v2 "cpu.max" ancestor quota.
  Integer millicores avoid floating-point rounding.
- Memory: the minimum of host "MemAvailable" and every capped ancestor's
  "memory.max - memory.current" (including charged cache, deliberately
  conservative). Keep reserves for the OS and the local Mojum Aquarium.
- Count: min(max_workers, CPU_slots, memory_slots), never above 300.
  Defaults budget one CPU and 768 MiB per proposed worker, with 2 CPUs
  and 1 GiB reserved for other work.

The probe refuses to guess on cgroup v1, missing membership, malformed quota,
or capped memory with unreadable usage. Kernel pressure, I/O, CPU frequency,
API rate limits and actual model RAM are outside this snapshot. These values
must be established by a small measured load test (4, then 16, then 32
workers) before raising concurrency. Do not infer the old server's capacity
from Mikoshi Tower hardware specs; probe the *actual* node and re-run after
VM/LXC quota or host load changes.

## Hermetic tests

~~~sh
python3 -m unittest discover -s tests -p 'test_spartan_host_capacity.py' -v
~~~

This is deliberately separate from the #477 dispatcher and from provider and
executor work on #478. It can be reviewed/merged without bringing up a single
model session or modifying production systems.
