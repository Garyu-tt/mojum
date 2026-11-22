"""Offline, deterministic resource admission plan for a bounded CPU Legion.

This tool does not provision hosts, start processes, or enforce resource limits.
"""

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path

INVENTORY_SCHEMA = "mojum.cpu-legion-inventory/1"
PLAN_SCHEMA = "mojum.cpu-legion-plan/1"
MAX_WORKERS = 300
MAX_NODES = 1000
MAX_INPUT_BYTES = 1_048_576
MAX_SAFE_INT = 2**53 - 1
_NODE_ID = re.compile(r"[A-Za-z0-9]+(?:[._:-][A-Za-z0-9]+)*", re.ASCII)


class InventoryError(ValueError):
    """Invalid or ambiguous host admission input."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _constant(value):
    raise InventoryError(f"non-JSON numeric constant: {value}")


def _fields(value, required, optional=()):
    if type(value) is not dict:
        raise InventoryError("expected JSON object")
    keys = set(value)
    missing = set(required) - keys
    extra = keys - set(required) - set(optional)
    if missing or extra:
        raise InventoryError(f"invalid fields: missing={sorted(missing)}, unexpected={sorted(extra)}")


def _integer(value, name, minimum, maximum=MAX_SAFE_INT):
    if type(value) is not int or not minimum <= value <= maximum:
        raise InventoryError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _node(value):
    required = ("id", "cpu_millicores", "memory_mib", "reserve_cpu_millicores",
                "reserve_memory_mib", "max_workers")
    _fields(value, required, ("enabled",))
    node_id = value["id"]
    if (type(node_id) is not str or not 1 <= len(node_id) <= 128
            or _NODE_ID.fullmatch(node_id) is None):
        raise InventoryError("node id must use canonical mesh id grammar")
    cpu = _integer(value["cpu_millicores"], "cpu_millicores", 1)
    ram = _integer(value["memory_mib"], "memory_mib", 1)
    reserve_cpu = _integer(value["reserve_cpu_millicores"], "reserve_cpu_millicores", 0)
    reserve_ram = _integer(value["reserve_memory_mib"], "reserve_memory_mib", 0)
    slots = _integer(value["max_workers"], "max_workers", 0, MAX_WORKERS)
    enabled = value.get("enabled", True)
    if type(enabled) is not bool:
        raise InventoryError("enabled must be boolean")
    if reserve_cpu > cpu or reserve_ram > ram:
        raise InventoryError(f"node {node_id}: reserve exceeds total capacity")
    return dict(id=node_id, cpu_millicores=cpu, memory_mib=ram,
                reserve_cpu_millicores=reserve_cpu,
                reserve_memory_mib=reserve_ram, max_workers=slots, enabled=enabled)


def load_inventory(path):
    """Read at most 1 MiB of strict UTF-8 JSON, rejecting duplicate keys."""
    # Opening a FIFO with Path.open() can block before any byte limit applies.
    # Inspect the opened inode and do not follow a final symlink on POSIX.
    descriptor = -1
    try:
        flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        descriptor = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise InventoryError("inventory must be a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            data = stream.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise InventoryError("cannot read inventory file") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > MAX_INPUT_BYTES:
        raise InventoryError("inventory exceeds 1 MiB")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_object,
                          parse_constant=_constant)
    except InventoryError:
        # Preserve precise duplicate-key and nonfinite-constant diagnostics.
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        # CPython may raise ValueError, not JSONDecodeError, for integer tokens
        # exceeding its configured digit limit (3.11+).
        raise InventoryError("inventory must be valid UTF-8 JSON") from exc


def plan(inventory, *, target=None):
    """Bound admissions by *both* CPU and RAM, with deterministic fair rounds."""
    _fields(inventory, ("schema", "target_workers", "worker", "nodes"))
    if inventory["schema"] != INVENTORY_SCHEMA:
        raise InventoryError("unsupported inventory schema")
    desired = _integer(inventory["target_workers"] if target is None else target,
                       "target_workers", 0, MAX_WORKERS)
    worker = inventory["worker"]
    _fields(worker, ("cpu_millicores", "memory_mib"))
    worker_cpu = _integer(worker["cpu_millicores"], "worker.cpu_millicores", 1)
    worker_ram = _integer(worker["memory_mib"], "worker.memory_mib", 1)
    nodes = inventory["nodes"]
    if type(nodes) is not list or not 1 <= len(nodes) <= MAX_NODES:
        raise InventoryError("nodes must be a nonempty list of at most 1000 nodes")
    result = []
    ids = set()
    for raw in nodes:
        node = _node(raw)
        if node["id"] in ids:
            raise InventoryError(f"duplicate node id: {node['id']}")
        ids.add(node["id"])
        usable_cpu = node["cpu_millicores"] - node["reserve_cpu_millicores"]
        usable_ram = node["memory_mib"] - node["reserve_memory_mib"]
        capacity = (min(node["max_workers"], usable_cpu // worker_cpu,
                        usable_ram // worker_ram) if node["enabled"] else 0)
        result.append({"id": node["id"], "enabled": node["enabled"],
                       "usable_cpu_millicores": usable_cpu,
                       "usable_memory_mib": usable_ram,
                       "capacity": capacity, "assigned": 0})
    result.sort(key=lambda node: node["id"])
    # At most 300 admissions; round robin to avoid concentrating all work on
    # the lexicographically first host. No host can exceed its computed cap.
    remaining = desired
    while remaining:
        progressed = False
        for node in result:
            if remaining == 0:
                break
            if node["assigned"] < node["capacity"]:
                node["assigned"] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    for node in result:
        node["planned_cpu_millicores"] = node["assigned"] * worker_cpu
        node["planned_memory_mib"] = node["assigned"] * worker_ram
        node["free_slots"] = node["capacity"] - node["assigned"]
    return {"schema": PLAN_SCHEMA, "requested": desired,
            "admitted": desired - remaining, "unplaced": remaining,
            "worker": {"cpu_millicores": worker_cpu, "memory_mib": worker_ram},
            "nodes": result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--target", type=int, help="override the requested worker count (0..300)")
    parser.add_argument("--require-full", action="store_true",
                        help="exit 2 if not all requested workers fit")
    args = parser.parse_args(argv)
    try:
        result = plan(load_inventory(args.inventory), target=args.target)
    except (InventoryError, OSError) as exc:
        print(f"cpu-legion: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 2 if args.require_full and result["unplaced"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
