#!/usr/bin/env python3
"""Read-only Linux cgroup-v2 capacity preflight for a prospective CPU Legion.

This is an estimate, NOT a worker launcher, admission controller or proof of
concurrent model sessions. An executor must enforce budgets independently.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath

MIB = 1024 * 1024
MAX_SPARTANS = 300
SCHEMA = "mojum.spartan.host-capacity/1"


class ProbeError(ValueError):
    """Resource limits could not be inspected without guessing."""


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise ProbeError(f"cannot read {path}: {exc}") from exc


def _positive_int(raw: str, description: str) -> int:
    if not raw.isascii() or not raw.isdecimal():
        raise ProbeError(f"invalid {description}")
    value = int(raw)
    if value <= 0:
        raise ProbeError(f"invalid {description}")
    return value


def _cgroup_ancestors(proc_root: Path, cgroup_root: Path) -> list[Path]:
    if _read(cgroup_root / "cgroup.controllers") is None:
        raise ProbeError("cgroup v2 mount not found; cannot certify v1 limits")
    groups = _read(proc_root / "self/cgroup")
    if groups is None:
        raise ProbeError("missing /proc/self/cgroup")
    matches = [entry[3:] for entry in groups.splitlines() if entry.startswith("0::")]
    if len(matches) != 1 or not matches[0].startswith("/"):
        raise ProbeError("missing or ambiguous cgroup v2 membership")
    member = PurePosixPath(matches[0])
    if ".." in member.parts:
        raise ProbeError("invalid cgroup membership path")
    try:
        root = cgroup_root.resolve(strict=True)
        leaf = root.joinpath(*member.parts[1:]).resolve(strict=True)
    except OSError as exc:
        raise ProbeError("cgroup v2 membership directory inaccessible") from exc
    if not leaf.is_relative_to(root):
        raise ProbeError("cgroup membership escapes mount")
    ancestors: list[Path] = []
    node = leaf
    while True:
        ancestors.append(node)
        if node == root:
            return ancestors
        node = node.parent


def _mem_available(proc_root: Path) -> int:
    meminfo = _read(proc_root / "meminfo")
    if meminfo is None:
        raise ProbeError("missing /proc/meminfo")
    found = [line.split() for line in meminfo.splitlines()
             if line.startswith("MemAvailable:")]
    if len(found) != 1 or len(found[0]) != 3 or found[0][2] != "kB":
        raise ProbeError("cannot read MemAvailable from procfs")
    return _positive_int(found[0][1], "MemAvailable") * 1024


def _cgroup_limits(ancestors: list[Path]) -> tuple[int | None, int | None]:
    """Return the tightest millicore quota and conservative free memory bytes."""
    cpu_milli: int | None = None
    memory_bytes: int | None = None
    for node in ancestors:
        raw_cpu = _read(node / "cpu.max")
        if raw_cpu is not None:
            tokens = raw_cpu.split()
            if len(tokens) != 2:
                raise ProbeError("invalid cpu.max")
            period = _positive_int(tokens[1], "cpu.max period")
            if tokens[0] != "max":
                quota = _positive_int(tokens[0], "cpu.max quota")
                quota_milli = quota * 1000 // period
                cpu_milli = quota_milli if cpu_milli is None else min(cpu_milli, quota_milli)
        raw_limit = _read(node / "memory.max")
        if raw_limit is not None and raw_limit != "max":
            maximum = _positive_int(raw_limit, "memory.max")
            current = _read(node / "memory.current")
            if current is None or not current.isascii() or not current.isdecimal():
                raise ProbeError("invalid or missing memory.current for capped cgroup")
            remaining = max(0, maximum - int(current))
            memory_bytes = remaining if memory_bytes is None else min(memory_bytes, remaining)
    return cpu_milli, memory_bytes


def estimate(
    *,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    affinity_cpus: int | None = None,
    worker_cpu_milli: int = 1000,
    worker_mem_mib: int = 768,
    reserve_cpu_milli: int = 2000,
    reserve_mem_mib: int = 1024,
    max_workers: int = MAX_SPARTANS,
) -> dict:
    """Conservative instantaneous upper bound. No process or state is modified."""
    for label, value, minimum in (
        ("worker_cpu_milli", worker_cpu_milli, 1),
        ("worker_mem_mib", worker_mem_mib, 1),
        ("reserve_cpu_milli", reserve_cpu_milli, 0),
        ("reserve_mem_mib", reserve_mem_mib, 0),
        ("max_workers", max_workers, 1),
    ):
        if type(value) is not int or value < minimum:
            raise ValueError(f"{label} must be an integer >= {minimum}")
    if max_workers > MAX_SPARTANS:
        raise ValueError("max_workers cannot exceed 300")
    if affinity_cpus is None:
        try:
            affinity_cpus = len(os.sched_getaffinity(0))
        except (OSError, AttributeError) as exc:
            raise ProbeError("Linux CPU affinity unavailable") from exc
    if type(affinity_cpus) is not int or affinity_cpus <= 0:
        raise ProbeError("invalid CPU affinity")

    ancestors = _cgroup_ancestors(proc_root, cgroup_root)
    quota_milli, cgroup_free_bytes = _cgroup_limits(ancestors)
    effective_cpu_milli = min(affinity_cpus * 1000, quota_milli) if quota_milli is not None else affinity_cpus * 1000
    proc_free_bytes = _mem_available(proc_root)
    free_bytes = min(proc_free_bytes, cgroup_free_bytes) if cgroup_free_bytes is not None else proc_free_bytes
    cpu_slots = max(0, (effective_cpu_milli - reserve_cpu_milli) // worker_cpu_milli)
    mem_slots = max(0, (free_bytes - reserve_mem_mib * MIB) // (worker_mem_mib * MIB))
    candidates = min(max_workers, cpu_slots, mem_slots)
    return {
        "schema": SCHEMA,
        "platform": "linux-cgroup-v2",
        "affinity_cpus": affinity_cpus,
        "effective_cpu_milli": effective_cpu_milli,
        "available_memory_mib": free_bytes // MIB,
        "cgroup_cpu_quota_seen": quota_milli is not None,
        "cgroup_memory_cap_seen": cgroup_free_bytes is not None,
        "parameters": {
            "worker_cpu_milli": worker_cpu_milli,
            "worker_mem_mib": worker_mem_mib,
            "reserve_cpu_milli": reserve_cpu_milli,
            "reserve_mem_mib": reserve_mem_mib,
            "max_workers": max_workers,
        },
        "cpu_limited_slots": cpu_slots,
        "memory_limited_slots": mem_slots,
        "planned_slot_ceiling": candidates,
        "running_model_sessions": "not measured",
        "capacity_is_enforced": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-cpu-milli", type=int, default=1000)
    parser.add_argument("--worker-mem-mib", type=int, default=768)
    parser.add_argument("--reserve-cpu-milli", type=int, default=2000)
    parser.add_argument("--reserve-mem-mib", type=int, default=1024)
    parser.add_argument("--max-workers", type=int, default=MAX_SPARTANS)
    args = parser.parse_args(argv)
    try:
        result = estimate(**vars(args))
    except (ProbeError, ValueError) as exc:
        print(json.dumps({"schema": SCHEMA, "status": "unavailable", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
