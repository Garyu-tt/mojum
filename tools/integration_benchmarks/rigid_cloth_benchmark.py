#!/usr/bin/env python3
"""Offline, dependency-free benchmark record validator for issue #702.

This module does not run or emulate a physics engine. It validates externally
measured, explicitly labelled observations and produces a reproducible summary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

SCHEMA = "mojum.rigid-cloth-benchmark/1"
ENGINES = frozenset({"mujoco-flex", "pinocchio-ik", "jolt-softbody", "pbd-xpbd"})
MAX_RUNS = 10000
MAX_NAME = 80
MAX_CPU_MS = 60_000.0
MAX_RSS_MIB = 1_048_576.0
MAX_INPUT_BYTES = 4 * 1024 * 1024


class BenchmarkError(ValueError):
    """Invalid or unsafe benchmark input."""


def _exact_int(value: Any, field: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise BenchmarkError(f"{field}: expected integer in [{low}, {high}]")
    return value


def _finite_number(value: Any, field: str, low: float, high: float) -> float:
    if type(value) not in (int, float):
        raise BenchmarkError(f"{field}: expected finite number")
    try:
        if not math.isfinite(value):
            raise BenchmarkError(f"{field}: expected finite number")
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        raise BenchmarkError(f"{field}: expected finite number") from None
    if not low <= result <= high:
        raise BenchmarkError(f"{field}: outside [{low}, {high}]")
    return result


def _name(value: Any, field: str) -> str:
    if type(value) is not str or not value or len(value) > MAX_NAME:
        raise BenchmarkError(f"{field}: expected non-empty string <= {MAX_NAME} chars")
    if any(ord(ch) < 32 for ch in value):
        raise BenchmarkError(f"{field}: control characters are forbidden")
    return value


def _exact_mapping(value: Any, required: set[str], field: str) -> dict[str, Any]:
    if type(value) is not dict or len(value) != len(required):
        raise BenchmarkError(f"{field}: expected exactly {len(required)} fields")
    supplied = tuple(value.keys())
    maximum_key_length = max(map(len, required))
    if any(type(key) is not str or len(key) > maximum_key_length for key in supplied):
        raise BenchmarkError(f"{field}: keys must be bounded native strings")
    if set(supplied) != required:
        raise BenchmarkError(f"{field}: fields do not match schema")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate keys instead of last-wins."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _percentile_nearest_rank(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered) / 100) - 1)
    return ordered[index]


def validate_document(document: Any) -> dict[str, Any]:
    """Validate a PUBLIC_SYNTHETIC benchmark document and return detached data."""
    document = _exact_mapping(document, {
        "schema", "provenance", "consent", "rig", "engine", "runs"
    }, "document")
    if type(document["schema"]) is not str or document["schema"] != SCHEMA:
        raise BenchmarkError("unsupported schema")
    if type(document["provenance"]) is not str or document["provenance"] != "PUBLIC_SYNTHETIC":
        raise BenchmarkError("only PUBLIC_SYNTHETIC fixtures are accepted")
    if document["consent"] is not True:
        raise BenchmarkError("explicit consent=true is required")

    rig = _exact_mapping(document["rig"], {"asset_id", "revision", "dof", "vertices", "constraints"}, "rig")
    clean_rig = {
        "asset_id": _name(rig["asset_id"], "rig.asset_id"),
        "revision": _name(rig["revision"], "rig.revision"),
        "dof": _exact_int(rig["dof"], "rig.dof", 1, 100_000),
        "vertices": _exact_int(rig["vertices"], "rig.vertices", 0, 2_000_000),
        "constraints": _exact_int(rig["constraints"], "rig.constraints", 0, 10_000_000),
    }

    engine = _exact_mapping(document["engine"], {"name", "version", "threads", "platform"}, "engine")
    engine_name = _name(engine["name"], "engine.name")
    if engine_name not in ENGINES:
        raise BenchmarkError("unsupported engine name")
    clean_engine = {
        "name": engine_name,
        "version": _name(engine["version"], "engine.version"),
        "threads": _exact_int(engine["threads"], "engine.threads", 1, 256),
        "platform": _name(engine["platform"], "engine.platform"),
    }

    runs = document["runs"]
    if type(runs) is not list or not 1 <= len(runs) <= MAX_RUNS:
        raise BenchmarkError(f"runs must contain 1..{MAX_RUNS} observations")
    clean_runs = []
    for index, run in enumerate(runs):
        run = _exact_mapping(run, {"cpu_ms", "rss_mib", "ik_residual", "limit_violations", "contact_penetration_mm"}, f"runs[{index}]")
        residual = run["ik_residual"]
        if residual is not None:
            residual = _finite_number(residual, f"runs[{index}].ik_residual", 0.0, 1e12)
        clean_runs.append({
            "cpu_ms": _finite_number(run["cpu_ms"], f"runs[{index}].cpu_ms", 0.0, MAX_CPU_MS),
            "rss_mib": _finite_number(run["rss_mib"], f"runs[{index}].rss_mib", 0.0, MAX_RSS_MIB),
            "ik_residual": residual,
            "limit_violations": _exact_int(run["limit_violations"], f"runs[{index}].limit_violations", 0, 1_000_000),
            "contact_penetration_mm": _finite_number(run["contact_penetration_mm"], f"runs[{index}].contact_penetration_mm", 0.0, 1e9),
        })
    return {"schema": SCHEMA, "provenance": "PUBLIC_SYNTHETIC", "consent": True,
            "rig": clean_rig, "engine": clean_engine, "runs": clean_runs}


def summarize(document: Any) -> dict[str, Any]:
    """Return stable aggregate statistics plus a SHA-256 consistency receipt."""
    clean = validate_document(document)
    cpu = [run["cpu_ms"] for run in clean["runs"]]
    rss = [run["rss_mib"] for run in clean["runs"]]
    residuals = [run["ik_residual"] for run in clean["runs"] if run["ik_residual"] is not None]
    payload = {
        "schema": SCHEMA,
        "provenance": "PUBLIC_SYNTHETIC",
        "engine": clean["engine"],
        "rig": clean["rig"],
        "sample_count": len(clean["runs"]),
        "cpu_ms": {"median": statistics.median(cpu), "p95": _percentile_nearest_rank(cpu, 95), "max": max(cpu)},
        "rss_mib": {"median": statistics.median(rss), "max": max(rss)},
        "ik_residual": {"sample_count": len(residuals), "max": max(residuals) if residuals else None},
        "limit_violations": sum(run["limit_violations"] for run in clean["runs"]),
        "contact_penetration_mm_max": max(run["contact_penetration_mm"] for run in clean["runs"]),
        "measurement_status": "externally_measured_input_not_verified_by_this_tool",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    payload["sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="PUBLIC_SYNTHETIC benchmark JSON")
    parser.add_argument("--output", type=Path, help="write summary JSON (default: stdout)")
    args = parser.parse_args(argv)
    try:
        with args.input.open("rb") as source:
            raw_bytes = source.read(MAX_INPUT_BYTES + 1)
        if len(raw_bytes) > MAX_INPUT_BYTES:
            raise BenchmarkError(f"input exceeds {MAX_INPUT_BYTES} byte limit")
        raw = raw_bytes.decode("utf-8")
        document = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(BenchmarkError(f"invalid constant {value}")),
        )
        report = summarize(document)
        rendered = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except (OSError, UnicodeError, json.JSONDecodeError, BenchmarkError) as exc:
        print(f"benchmark rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
