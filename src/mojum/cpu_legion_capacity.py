"""Read-only, fail-closed Linux cgroup-v2 CPU Legion capacity preflight.

This is an advisory planner, not a scheduler or proof of available model
capacity. It deliberately does not launch processes or contact a provider.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

SCHEMA = "mojum.cpu-legion.capacity/1"
MAX_TARGET = 300


class CapacityError(ValueError):
    """Unable to establish a reliable local resource bound."""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise CapacityError(f"resource metric unavailable: {path.name}") from exc


def _nonnegative(text: str, name: str) -> int:
    if not text.isascii() or not text.isdecimal():
        raise CapacityError(f"invalid {name} metric")
    return int(text)


def _current_cgroup(root: Path, proc_cgroup: Path) -> Path:
    """Resolve the current process's unified cgroup inside its visible mount."""
    rows = _read(proc_cgroup).splitlines()
    entries = [row[3:] for row in rows if row.startswith("0::")]
    if len(entries) != 1:
        raise CapacityError("unified cgroup v2 membership not available")
    path = entries[0]
    if not path.startswith("/") or any(p in (".", "..") for p in path.split("/")):
        raise CapacityError("invalid process cgroup path")
    candidate = root.joinpath(*[p for p in path.split("/") if p])
    required = ("cpu.max", "memory.max", "memory.current")
    if candidate.is_dir():
        # An existing membership scope with missing metrics is incomplete.
        # Never fall back to a less-restricted ancestor: that can inflate
        # CPU/RAM admission when a controller or file is unavailable.
        if not all((candidate / name).is_file() for name in required):
            raise CapacityError("current cgroup v2 resource files unavailable")
        return candidate
    # A missing membership path cannot be verified against this visible
    # mount. Treating the root as this process's scope might silently ignore
    # a tighter per-process quota, inflating admission capacity.
    raise CapacityError("current cgroup v2 membership path is not visible")


def _scope_chain(current: Path, root: Path) -> list[Path]:
    root = root.resolve()
    current = current.resolve()
    try:
        current.relative_to(root)
    except ValueError as exc:
        raise CapacityError("cgroup directory escapes visible mount") from exc
    chain = [current]
    while chain[-1] != root:
        chain.append(chain[-1].parent)
    return chain


def discover(*, meminfo: Path = Path("/proc/meminfo"),
             proc_cgroup: Path = Path("/proc/self/cgroup"),
             cgroup_root: Path = Path("/sys/fs/cgroup"),
             affinity_cores: int | None = None) -> dict:
    """Report bounded local CPU millicores and available MiB, never totals alone."""
    raw = _read(meminfo)
    matches = re.findall(r"^MemAvailable:\s*([0-9]+)\s+kB\s*$", raw, re.M)
    if len(matches) != 1:
        raise CapacityError("MemAvailable missing or ambiguous")
    system_available = int(matches[0]) * 1024
    if affinity_cores is None:
        try:
            affinity_cores = len(os.sched_getaffinity(0))
        except (AttributeError, OSError) as exc:
            raise CapacityError("CPU affinity unavailable") from exc
    if type(affinity_cores) is not int or affinity_cores <= 0:
        raise CapacityError("invalid CPU affinity")
    current = _current_cgroup(cgroup_root, proc_cgroup)
    scopes = _scope_chain(current, cgroup_root)
    cpu_millicores = affinity_cores * 1000
    memory_available = system_available
    for scope in scopes:
        cpu = _read(scope / "cpu.max").split()
        if len(cpu) != 2:
            raise CapacityError("invalid cpu.max metric")
        quota, period = cpu
        period_num = _nonnegative(period, "cpu.max period")
        if not period_num:
            raise CapacityError("zero cpu.max period")
        if quota != "max":
            quota_num = _nonnegative(quota, "cpu.max quota")
            cpu_millicores = min(cpu_millicores, quota_num * 1000 // period_num)
        maximum = _read(scope / "memory.max")
        used = _nonnegative(_read(scope / "memory.current"), "memory.current")
        if maximum != "max":
            limit = _nonnegative(maximum, "memory.max")
            memory_available = min(memory_available, max(0, limit - used))
    return {
        "cpu_millicores": cpu_millicores,
        "available_memory_mib": memory_available // (1024 * 1024),
        "affinity_cores": affinity_cores,
        "cgroup_scopes_checked": len(scopes),
    }


def plan(snapshot: dict, *, target: int, worker_mib: int,
         worker_millicores: int, reserve_mib: int,
         reserve_millicores: int) -> dict:
    """Make a conservative integer budget without allocating anything."""
    for name, value, minimum in (
        ("target", target, 1), ("worker_mib", worker_mib, 1),
        ("worker_millicores", worker_millicores, 1),
        ("reserve_mib", reserve_mib, 0),
        ("reserve_millicores", reserve_millicores, 0),
    ):
        if type(value) is not int or value < minimum:
            raise CapacityError(f"{name} must be an integer >= {minimum}")
    if target > MAX_TARGET:
        raise CapacityError(f"target exceeds maximum {MAX_TARGET}")
    cpu = snapshot.get("cpu_millicores")
    memory = snapshot.get("available_memory_mib")
    if any(type(v) is not int or v < 0 for v in (cpu, memory)):
        raise CapacityError("invalid resource snapshot")
    by_cpu = max(0, cpu - reserve_millicores) // worker_millicores
    by_memory = max(0, memory - reserve_mib) // worker_mib
    slots = min(target, by_cpu, by_memory)
    return {
        "schema": SCHEMA,
        "status": "advisory_only_no_workers_started",
        "requested_workers": target,
        "safe_budget_slots": slots,
        "resource_snapshot": snapshot,
        "per_worker": {"memory_mib": worker_mib, "cpu_millicores": worker_millicores},
        "reserved_for_host": {"memory_mib": reserve_mib, "cpu_millicores": reserve_millicores},
        "ceilings": {"cpu": by_cpu, "memory": by_memory},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=MAX_TARGET)
    parser.add_argument("--worker-mib", type=int, required=True)
    parser.add_argument("--worker-millicores", type=int, required=True)
    parser.add_argument("--reserve-mib", type=int, default=2048)
    parser.add_argument("--reserve-millicores", type=int, default=1000)
    args = parser.parse_args(argv)
    try:
        result = plan(discover(), target=args.target,
                      worker_mib=args.worker_mib,
                      worker_millicores=args.worker_millicores,
                      reserve_mib=args.reserve_mib,
                      reserve_millicores=args.reserve_millicores)
    except CapacityError as exc:
        print(json.dumps({"schema": SCHEMA, "status": "unavailable", "error": str(exc)}),
              file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
