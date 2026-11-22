#!/usr/bin/env python3
"""Read-only GPU placement plan for multiple gpt-oss-20b GGUF servers.

No model downloads, subprocess launches (except read-only nvidia-smi),
network requests, credential access or GitHub writes.
"""
from __future__ import annotations

import argparse
import csv
from functools import lru_cache
import itertools
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

MAX_GPUS = 32
MAX_INPUT_BYTES = 65536
DEFAULT_MODEL_MIB = 15360
DEFAULT_RESERVE_MIB = 768
MAX_GPUS_PER_INSTANCE = 4
DEFAULT_BASE_PORT = 18180  # ALT is already bound to 18080 during staging


class InventoryError(ValueError):
    """Untrusted GPU evidence cannot be used for capacity planning."""


def _unique_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise InventoryError("duplicate JSON key: " + str(key))
        out[key] = value
    return out



def _optimal_placement(devices, model_mib, reserve_mib, max_gpus):
    """Exact, deterministic maximum-replica set packing for at most 12 GPUs.

    The old smallest-cardinality-first greedy selection can strand small
    devices: 9+9 GiB occupies both large GPUs, although 9+3+3 twice fits.
    Cache by used-device mask and allow skipping a device entirely.
    """
    indices = sorted(devices)
    if len(indices) > 12:
        raise InventoryError("exact placement accepts at most 12 GPUs")
    usable = {i: max(0, devices[i]["free_mib"] - reserve_mib) for i in indices}
    n = len(indices)

    def score(groups):
        return (
            -len(groups),
            sum(len(group) for group in groups),
            sum(sum(usable[i] for i in group) - model_mib for group in groups),
            groups,
        )

    @lru_cache(maxsize=None)
    def search(mask):
        first = next((i for i in range(n) if not mask & (1 << i)), None)
        if first is None:
            return ()
        best = search(mask | (1 << first))
        remaining = [i for i in range(first + 1, n) if not mask & (1 << i)]
        for count in range(1, min(max_gpus, len(remaining) + 1) + 1):
            for tail in itertools.combinations(remaining, count - 1):
                positions = (first,) + tail
                candidate_group = tuple(indices[i] for i in positions)
                if sum(usable[i] for i in candidate_group) < model_mib:
                    continue
                group_mask = sum(1 << i for i in positions)
                candidate = (candidate_group,) + search(mask | group_mask)
                if score(candidate) < score(best):
                    best = candidate
        return best

    return search(0)


def validate_inventory(data):
    if not isinstance(data, dict) or data.get("source") not in ("synthetic", "nvidia-smi"):
        raise InventoryError("source must be synthetic or nvidia-smi")
    raw = data.get("gpus")
    if not isinstance(raw, list) or len(raw) > MAX_GPUS:
        raise InventoryError("gpus must be an array with at most 32 devices")
    result = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise InventoryError("GPU must be an object")
        index, name = item.get("index"), item.get("name")
        total, free = item.get("total_mib"), item.get("free_mib")
        cap = item.get("compute_cap")
        if type(index) is not int or index < 0 or index >= 256 or index in seen:
            raise InventoryError("GPU index invalid or duplicated")
        if not isinstance(name, str) or not name or len(name) > 150:
            raise InventoryError("GPU name invalid")
        if (type(total) is not int or type(free) is not int or
                not 0 <= free <= total <= 1048576 or total == 0):
            raise InventoryError("GPU memory must be a valid integer MiB value")
        if not isinstance(cap, str) or not re.fullmatch(r"[0-9]{1,2}\.[0-9]", cap):
            raise InventoryError("compute_cap must be major.minor")
        major, minor = (int(x) for x in cap.split("."))
        seen.add(index)
        result.append({"index": index, "name": name,
                       "total_mib": total, "free_mib": free,
                       "compute_cap": cap, "supported": (major, minor) >= (6, 1)})
    return sorted(result, key=lambda gpu: gpu["index"])


def load_inventory(path):
    """Read at most 64 KiB from a verified regular descriptor, never a FIFO.

    Path.read_bytes() performs an unbounded read before the old limit check.
    The size preflight and bounded descriptor read protect both memory and
    blocking device/FIFO paths, including a file that grows after fstat.
    """
    if not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC")):
        raise InventoryError("secure local inventory file reads unavailable")
    descriptor = os.open(os.fspath(path),
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise InventoryError("GPU inventory must be a regular file")
        if info.st_size > MAX_INPUT_BYTES:
            raise InventoryError("GPU inventory exceeds 64 KiB")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            raw = source.read(MAX_INPUT_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > MAX_INPUT_BYTES:
        raise InventoryError("GPU inventory exceeds 64 KiB")
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys)
    return data, validate_inventory(data)


def probe_nvidia_smi():
    command = ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free,compute_cap",
               "--format=csv,noheader,nounits"]
    try:
        result = subprocess.run(command, check=True, capture_output=True,
                                timeout=10, text=True)
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise InventoryError("nvidia-smi unavailable or unsuccessful: " + str(exc)) from exc
    if len(result.stdout) > MAX_INPUT_BYTES:
        raise InventoryError("nvidia-smi response oversized")
    devices = []
    for fields in csv.reader(result.stdout.splitlines()):
        if len(fields) != 5:
            raise InventoryError("unexpected nvidia-smi CSV shape")
        try:
            idx = int(fields[0].strip())
            total = int(fields[2].strip())
            free = int(fields[3].strip())
        except ValueError as exc:
            raise InventoryError("nvidia-smi returned noninteger memory/index") from exc
        devices.append({"index": idx, "name": fields[1].strip(),
                        "total_mib": total, "free_mib": free,
                        "compute_cap": fields[4].strip()})
    data = {"source": "nvidia-smi", "gpus": devices}
    return data, validate_inventory(data)


def plan(data, gpus, *, model_mib=DEFAULT_MODEL_MIB, reserve_mib=DEFAULT_RESERVE_MIB,
         max_gpus=MAX_GPUS_PER_INSTANCE, max_replicas=32, base_port=DEFAULT_BASE_PORT,
         ctx_size=2048, model_path="/models/gpt-oss-20b-mxfp4.gguf"):
    for field, number, lower, upper in (
        ("model_mib", model_mib, 4096, 262144),
        ("reserve_mib", reserve_mib, 256, 16384),
        ("max_gpus", max_gpus, 1, 4),
        ("max_replicas", max_replicas, 1, 32),
        ("base_port", base_port, 1024, 65000),
        ("ctx_size", ctx_size, 512, 32768),
    ):
        if type(number) is not int or not lower <= number <= upper:
            raise InventoryError(field + " is out of bounds")
    if base_port + max_replicas > 65535:
        raise InventoryError("port range exceeds 65535")
    if not isinstance(model_path, str) or not model_path or len(model_path) > 1024:
        raise InventoryError("invalid model path")

    unused = {gpu["index"]: gpu for gpu in gpus if gpu["supported"]}
    # Exact maximum-replica placement is bounded at 12 devices (4096 masks).
    # Larger fleets retain a deterministic greedy estimate, explicitly labeled.
    exact_groups = (_optimal_placement(unused, model_mib, reserve_mib, max_gpus)
                    if len(unused) <= 12 else None)
    groups = []
    while len(groups) < max_replicas:
        if exact_groups is not None:
            if len(groups) >= len(exact_groups):
                break
            chosen = tuple(unused[i] for i in exact_groups[len(groups)])
        else:
            chosen = None
            # Bounded heuristic only. This can underestimate a heterogeneous fleet.
            for cardinality in range(1, min(max_gpus, len(unused)) + 1):
                eligible = []
                for devices in itertools.combinations(unused.values(), cardinality):
                    usable = sum(max(0, d["free_mib"] - reserve_mib) for d in devices)
                    if usable >= model_mib:
                        eligible.append((usable - model_mib,
                                         tuple(d["index"] for d in devices), devices))
                if eligible:
                    chosen = min(eligible, key=lambda x: (x[0], x[1]))[2]
                    break
            if chosen is None:
                break
        indices = [d["index"] for d in chosen]
        for index in indices:
            del unused[index]
        replica = len(groups)
        command = [
            "llama-server", "-m", model_path, "--host", "127.0.0.1",
            "--port", str(base_port + replica),
            "--split-mode", "layer", "--n-gpu-layers", "99",
            "--ctx-size", str(ctx_size), "--parallel", "1", "--jinja",
        ]
        groups.append({
            "replica": replica,
            "gpu_indices": indices,
            "usable_vram_mib": sum(max(0, d["free_mib"] - reserve_mib) for d in chosen),
            "env": {"CUDA_VISIBLE_DEVICES": ",".join(str(x) for x in indices)},
            "argv": command,
            "endpoint": "http://127.0.0.1:" + str(base_port + replica) + "/v1/chat/completions",
        })

    warnings = ["Placement estimate only: no model loaded, no CUDA benchmark performed.",
                "GGUF file, llama.cpp CUDA build, kernel support and GPU memory peaks are unverified.",
                "Do not set --parallel above 1 before per-replica memory and throughput tests.",
                "Do not expose the model service beyond loopback; do not launch via untrusted shell input.",
                "Local GPU observation is neither cryptographic attestation nor deployment authorization."]
    if exact_groups is None:
        warnings.append("More than 12 supported GPUs: greedy estimate may undercount feasible replicas.")
    if any(g["supported"] and tuple(int(x) for x in g["compute_cap"].split(".")) < (7, 5)
           for g in gpus):
        warnings.append("Pascal/older GPUs need a compatible CUDA build; native MXFP4 hardware acceleration is unavailable.")
    return {
        "schema": "mojum.gpu-legion-plan.v1",
        "model": "openai/gpt-oss-20b GGUF (model file operator-supplied)",
        "source": data["source"], "hardware_verified": False,
        "observed_local_inventory": False,
        "model_budget_mib": model_mib, "per_gpu_reserve_mib": reserve_mib,
        "estimated_replicas": len(groups), "live_replicas": 0,
        "placement_strategy": "exact_max_replicas" if exact_groups is not None else "bounded_greedy",
        "deploy_performed": False, "ready_for_merge": False,
        "replicas": groups,
        "unused_gpu_indices": sorted(set(g["index"] for g in gpus) -
                                     set(i for g in groups for i in g["gpu_indices"])),
        "warnings": warnings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", help="Offline JSON inventory; otherwise probe nvidia-smi")
    parser.add_argument("--model", default="/models/gpt-oss-20b-mxfp4.gguf")
    parser.add_argument("--model-mib", type=int, default=DEFAULT_MODEL_MIB)
    parser.add_argument("--reserve-mib", type=int, default=DEFAULT_RESERVE_MIB)
    parser.add_argument("--max-gpus-per-instance", type=int, default=MAX_GPUS_PER_INSTANCE)
    parser.add_argument("--max-replicas", type=int, default=32)
    parser.add_argument("--require-replicas", type=int, default=0)
    parser.add_argument("--base-port", type=int, default=DEFAULT_BASE_PORT)
    parser.add_argument("--ctx-size", type=int, default=2048)
    args = parser.parse_args(argv)
    try:
        if type(args.require_replicas) is not int or not 0 <= args.require_replicas <= 32:
            raise InventoryError("require-replicas must be between 0 and 32")
        probed_live = args.inventory is None
        data, gpus = load_inventory(args.inventory) if args.inventory else probe_nvidia_smi()
        report = plan(data, gpus, model_mib=args.model_mib, reserve_mib=args.reserve_mib,
                      max_gpus=args.max_gpus_per_instance, max_replicas=args.max_replicas,
                      base_port=args.base_port, ctx_size=args.ctx_size, model_path=args.model)
        report["observed_local_inventory"] = probed_live
        report["shortfall"] = max(0, args.require_replicas - report["estimated_replicas"])
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2))
        return 2 if report["shortfall"] else 0
    except (InventoryError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"schema": "mojum.gpu-legion-plan.v1", "error": str(exc),
                          "deploy_performed": False, "estimated_replicas": 0}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
