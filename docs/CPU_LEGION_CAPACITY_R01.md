# CPU Legion R0.1: advisory capacity preflight (not worker deployment)

The standalone CPU-first Aquarium must retain enough resources to operate. Before enrolling an older Linux server as another worker host, run a **read-only capacity preflight**:

```sh
PYTHONPATH=src python3 -m mojum.cpu_legion_capacity \
  --target 300 --worker-mib 4096 --worker-millicores 1000 \
  --reserve-mib 4096 --reserve-millicores 2000
```

`--worker-mib` and `--worker-millicores` are **measured per-worker peak demands**, not advertised model parameters. The example values are illustrative, not validated for any particular server or model. Reservations protect Aquarium plus OS/other host duties. Re-measure after adding other services.

This command reads `/proc/meminfo`, `/proc/self/cgroup`, `sched_getaffinity`, and visible cgroup-v2 `cpu.max`, `memory.max`, `memory.current` at the process scope **and its visible ancestors**. It takes the minimum of host MemAvailable and remaining cgroup memory; CPU is bounded by affinity and cgroup quotas. All units are integer MiB or millicores, rounded *down*. If relevant metrics are missing/invalid it returns exit 2; it never silently treats missing limits as proof of capacity. `status=advisory_only_no_workers_started` means no agents were launched, and `safe_budget_slots` is a budget ceiling, not an actual concurrency measurement.

**Limits:** only supported on Linux cgroup v2; cgroup namespaces may hide outer parent constraints. The result is an **advisory planning snapshot**, not a kernel-enforced reservation. Memory pressure, NUMA, swap, thermal/power constraints, inference API quotas and GPU residency are outside the estimate. Do not equate 300 slots with 300 running models. An executor must enforce its own per-worker quotas and recheck health and resource usage before every admission. The preflight never reads credentials, makes network requests, changes host settings or launches workers.

See [Spartan 300 work packages](https://github.com/Garyu-tt/mojum/issues/478). This independent host preflight does not depend on PR #477 or the dispatcher and is safe to run offline.
