#!/usr/bin/env python3
"""Offline, metadata-only admission matrix for optional llama.cpp/LiteLLM.

This is a deterministic preflight/controlled spike, NOT an executor, not a
provider-side reservation, and not evidence that models have run. Profiles are
explicit operator-supplied synthetic or measured snapshots; no network calls.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re

SCHEMA = "mojum.spartan.provider-capacity/1"
MAX_PROFILE_BYTES = 65536
MAX_MODELS = 16
MAX_LEASES = 300
MAX_MIB = 1_048_576
MAX_TOKENS = 1_000_000_000
MAX_USD = 1_000_000_000_000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,95}\Z", re.ASCII)
_ENDPOINT = re.compile(r"http://127\.0\.0\.1:([0-9]{1,5})\Z", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class AdmissionError(ValueError):
    """Malformed snapshot or invalid preflight inputs; no request admitted."""


def _int(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise AdmissionError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _pairs(items):
    obj = {}
    for k, v in items:
        if k in obj:
            raise AdmissionError("duplicate JSON property")
        obj[k] = v
    return obj


def _constant(_):
    raise AdmissionError("non-finite JSON value")


def _fields(obj, expected, name):
    if type(obj) is not dict or set(obj) != set(expected):
        raise AdmissionError(f"{name} has missing or unknown properties")


@dataclass(frozen=True)
class Model:
    id: str
    transport: str
    model: str
    model_sha256: str
    runtime_version: str
    endpoint: str
    device: str
    ram_mib: int
    vram_mib: int
    slots_total: int
    slots_free: int
    context_tokens: int
    decode_tokens_per_sec: int
    micro_usd_per_1k_tokens: int


@dataclass(frozen=True)
class Snapshot:
    evidence: str
    models: tuple[Model, ...]


def parse_snapshot(raw: bytes) -> Snapshot:
    if type(raw) is not bytes or len(raw) > MAX_PROFILE_BYTES:
        raise AdmissionError("snapshot must be <=64 KiB bytes")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=_constant)
    except AdmissionError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        # Python 3.11+ raises bare ValueError for overlong JSON integer text.
        raise AdmissionError("invalid JSON snapshot") from exc
    _fields(data, ("schema", "evidence", "models"), "snapshot")
    if data["schema"] != SCHEMA:
        raise AdmissionError("snapshot schema mismatch")
    if data["evidence"] not in ("synthetic-fixture", "operator-measured"):
        raise AdmissionError("unknown evidence type")
    src = data["models"]
    if type(src) is not list or not 1 <= len(src) <= MAX_MODELS:
        raise AdmissionError("model count outside 1..16")
    models = []
    ids = set()
    endpoints = set()
    expected = tuple(Model.__dataclass_fields__)
    for row in src:
        _fields(row, expected, "model")
        ident = row["id"]
        if type(ident) is not str or not _ID.fullmatch(ident) or ident in ids:
            raise AdmissionError("duplicate or unsafe model id")
        ids.add(ident)
        model = row["model"]
        version = row["runtime_version"]
        if (type(model) is not str or not _MODEL.fullmatch(model)
                or ".." in model or type(version) is not str
                or not _ID.fullmatch(version)
                or version.lower() in ("latest", "main", "master")):
            raise AdmissionError("model and runtime must have pinned safe identity")
        digest = row["model_sha256"]
        if type(digest) is not str or not _HASH.fullmatch(digest):
            raise AdmissionError("model digest must be 64 lowercase hex")
        if row["transport"] not in ("llama.cpp", "litellm"):
            raise AdmissionError("unapproved model transport")
        if row["device"] not in ("cpu", "gpu"):
            raise AdmissionError("unapproved device type")
        endpoint = row["endpoint"]
        match = _ENDPOINT.fullmatch(endpoint) if type(endpoint) is str else None
        if (not match or not 1 <= int(match.group(1)) <= 65535
                or endpoint != f"http://127.0.0.1:{int(match.group(1))}"
                or int(match.group(1)) in endpoints):
            raise AdmissionError("endpoint must be a unique literal loopback HTTP port")
        endpoints.add(int(match.group(1)))
        for key, a, b in (("ram_mib", 0, MAX_MIB),
                          ("vram_mib", 0, MAX_MIB),
                          ("slots_total", 1, 16),
                          ("slots_free", 0, 16),
                          ("context_tokens", 128, 1_000_000),
                          ("decode_tokens_per_sec", 1, 1_000_000),
                          ("micro_usd_per_1k_tokens", 0, MAX_USD)):
            _int(row[key], key, a, b)
        if row["slots_free"] > row["slots_total"]:
            raise AdmissionError("free slots exceed resident model capacity")
        if row["device"] == "cpu" and row["vram_mib"] != 0:
            raise AdmissionError("CPU model cannot claim dedicated VRAM")
        models.append(Model(**row))
    return Snapshot(data["evidence"], tuple(sorted(models, key=lambda m: m.id)))


def validate_live_slots(snapshot: Snapshot, backends) -> None:
    """Refuse stale/mismatched llama.cpp /slots inventory, without probing it.

    Accepts existing spartan_local_fleet.Backend objects, checked by caller.
    LiteLLM is excluded: it does not expose llama.cpp's slot protocol.
    """
    lookup = {(b.port, b.model): b for b in backends}
    for model in snapshot.models:
        if model.transport != "llama.cpp":
            continue
        port = int(model.endpoint.rsplit(":", 1)[1])
        backend = lookup.get((port, model.model))
        if (backend is None or type(backend.total) is not int
                or type(backend.free) is not int
                or backend.total != model.slots_total
                or backend.free < model.slots_free
                or backend.free > backend.total
                or backend.ctx < model.context_tokens):
            raise AdmissionError("live llama.cpp slots disagree with inventory; remeasure")


def matrix(snapshot: Snapshot, *, counts=(1, 2, 4, 8), worker_leases=300,
           ram_budget_mib, vram_budget_mib, token_budget, micro_usd_budget,
           prompt_tokens, completion_tokens, window_seconds, max_retries=0):
    """Compute conservative independent burst plans; never authorizes RPC."""
    _int(worker_leases, "worker_leases", 1, MAX_LEASES)
    _int(ram_budget_mib, "ram_budget_mib", 1, MAX_MIB)
    _int(vram_budget_mib, "vram_budget_mib", 0, MAX_MIB)
    _int(token_budget, "token_budget", 1, MAX_TOKENS)
    _int(micro_usd_budget, "micro_usd_budget", 0, MAX_USD)
    _int(prompt_tokens, "prompt_tokens", 1, 1_000_000)
    _int(completion_tokens, "completion_tokens", 1, 1_000_000)
    _int(window_seconds, "window_seconds", 1, 3600)
    _int(max_retries, "max_retries", 0, 2)
    if type(counts) not in (tuple, list) or not 1 <= len(counts) <= 16:
        raise AdmissionError("invalid burst list")
    for n in counts:
        _int(n, "burst", 1, MAX_LEASES)
    if len(set(counts)) != len(counts):
        raise AdmissionError("duplicate burst size")

    total_ram = sum(m.ram_mib for m in snapshot.models)
    total_vram = sum(m.vram_mib for m in snapshot.models)
    # All resident models consume resources even when free slots == 0.
    resident_ok = total_ram <= ram_budget_mib and total_vram <= vram_budget_mib
    attempts = 1 + max_retries
    reserved_one = (prompt_tokens + completion_tokens) * attempts
    out_one = completion_tokens * attempts
    results = []
    for requested in counts:
        left_slots = {m.id: m.slots_free for m in snapshot.models}
        left_decode = {m.id: m.decode_tokens_per_sec * window_seconds
                       for m in snapshot.models}
        assignment = {m.id: 0 for m in snapshot.models}
        reserved_tokens = 0
        reserved_usd = 0
        reasons = {}
        for request_index in range(requested):
            reason = None
            if request_index >= worker_leases:
                reason = "worker_lease_bound"
            elif not resident_ok:
                reason = "resident_memory_over_budget"
            elif reserved_one > token_budget - reserved_tokens:
                reason = "token_reservation_exhausted"
            else:
                eligible = [m for m in snapshot.models
                            if prompt_tokens + completion_tokens <= m.context_tokens
                            and left_slots[m.id] > 0 and left_decode[m.id] >= out_one]
                # Prefer zero-cost eligible models, then deterministic ids.
                eligible.sort(key=lambda m: (m.micro_usd_per_1k_tokens, m.id))
                chosen = next((m for m in eligible if
                               ((reserved_one * m.micro_usd_per_1k_tokens + 999) // 1000)
                               <= micro_usd_budget - reserved_usd), None)
                if chosen is None:
                    if eligible:
                        reason = "usd_reservation_exhausted"
                    elif all(prompt_tokens + completion_tokens > m.context_tokens
                             for m in snapshot.models):
                        reason = "context_exceeded"
                    else:
                        reason = "slots_or_decode_capacity_exhausted"
                else:
                    cost = ((reserved_one * chosen.micro_usd_per_1k_tokens + 999)
                            // 1000)
                    assignment[chosen.id] += 1
                    left_slots[chosen.id] -= 1
                    left_decode[chosen.id] -= out_one
                    reserved_tokens += reserved_one
                    reserved_usd += cost
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
        admitted = sum(assignment.values())
        results.append({"requested": requested, "admitted": admitted,
                        "denied": requested - admitted,
                        "reasons": dict(sorted(reasons.items())),
                        "placements": {k: v for k, v in assignment.items() if v},
                        "reserved_tokens": reserved_tokens,
                        "reserved_microusd": reserved_usd})
    return {"schema": "mojum.spartan.provider-matrix/1", "simulation_only": True,
            "evidence": snapshot.evidence, "worker_leases": worker_leases,
            "resident_models": len(snapshot.models),
            "resident_ram_mib": total_ram, "resident_vram_mib": total_vram,
            "max_retries_reserved": max_retries, "window_seconds": window_seconds,
            "bursts": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--worker-leases", type=int, default=300)
    parser.add_argument("--ram-mib", type=int, required=True)
    parser.add_argument("--vram-mib", type=int, default=0)
    parser.add_argument("--tokens", type=int, required=True)
    parser.add_argument("--micro-usd", type=int, default=0)
    parser.add_argument("--prompt-tokens", type=int, default=256)
    parser.add_argument("--completion-tokens", type=int, default=256)
    parser.add_argument("--window-seconds", type=int, default=60)
    parser.add_argument("--max-retries", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        if not args.snapshot.is_file() or args.snapshot.stat().st_size > MAX_PROFILE_BYTES:
            raise AdmissionError("snapshot is missing or oversized")
        snapshot = parse_snapshot(args.snapshot.read_bytes())
        result = matrix(snapshot, worker_leases=args.worker_leases,
                        ram_budget_mib=args.ram_mib, vram_budget_mib=args.vram_mib,
                        token_budget=args.tokens, micro_usd_budget=args.micro_usd,
                        prompt_tokens=args.prompt_tokens,
                        completion_tokens=args.completion_tokens,
                        window_seconds=args.window_seconds,
                        max_retries=args.max_retries)
    except (AdmissionError, OSError) as exc:
        parser.exit(2, "provider admission denied: " + str(exc) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
