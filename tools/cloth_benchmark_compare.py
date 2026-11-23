#!/usr/bin/env python3
"""Bounded offline comparator for caller-supplied PUBLIC_SYNTHETIC cloth measurements."""
from __future__ import annotations
import argparse, hashlib, json, math, re, sys
from pathlib import Path
from typing import Any

SCHEMA = "mojum.cloth-benchmark-report/1"
MAX_BYTES, MAX_RUNS, MAX_SAMPLES = 1_048_576, 16, 1_000
IDENTITY = ("scenario_id", "seed", "mesh_sha256", "vertices", "constraints", "dt_ns", "steps", "warmup_steps")
SHA = re.compile(r"^[0-9a-f]{64}$")
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+/-]{0,95}$")


class ReportError(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReportError("duplicate JSON object key")
        result[key] = value
    return result


def _obj(value, name, keys):
    if type(value) is not dict or any(type(k) is not str for k in value) or set(value) != keys:
        raise ReportError(f"{name}: missing, unknown or invalid object keys")
    return value


def _int(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ReportError(f"{name}: expected integer in [{low}, {high}]")
    return value


def _ident(value, name, limit=96):
    if type(value) is not str or not value.isascii() or not IDENT.fullmatch(value) or len(value) > limit:
        raise ReportError(f"{name}: invalid bounded ASCII identifier")
    return value


def _number(value, name):
    if type(value) not in (int, float):
        raise ReportError(f"{name}: expected native number")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ReportError(f"{name}: number out of range") from None
    if not math.isfinite(number) or not 0 <= number <= 3_600_000_000:
        raise ReportError(f"{name}: expected finite nonnegative microseconds")
    return number


def _rank(values, p):
    ordered = sorted(values)
    import math as _math
    return ordered[max(0, _math.ceil(p * len(ordered)) - 1)]


def analyze(value: Any) -> dict:
    root = _obj(value, "document", {"schema", "visibility", "runs"})
    if type(root["schema"]) is not str or root["schema"] != SCHEMA:
        raise ReportError("unsupported schema")
    if type(root["visibility"]) is not str or root["visibility"] != "PUBLIC_SYNTHETIC":
        raise ReportError("only PUBLIC_SYNTHETIC input is accepted")
    raw_runs = root["runs"]
    if type(raw_runs) is not list or not 2 <= len(raw_runs) <= MAX_RUNS:
        raise ReportError(f"runs must contain 2..{MAX_RUNS} entries")
    checked = []
    samples_total = 0
    for index, raw in enumerate(raw_runs):
        name = f"runs[{index}]"
        fields = set(IDENTITY) | {"engine", "engine_version", "cpu_samples_us", "rss_peak_bytes", "penetration_count", "dropped_steps"}
        item = _obj(raw, name, fields)
        engine = _ident(item["engine"], name + ".engine", 64)
        version = _ident(item["engine_version"], name + ".engine_version", 64)
        scenario = _ident(item["scenario_id"], name + ".scenario_id")
        seed = _int(item["seed"], name + ".seed", 0, 2**31 - 1)
        digest = item["mesh_sha256"]
        if type(digest) is not str or not SHA.fullmatch(digest):
            raise ReportError(name + ".mesh_sha256: expected lowercase SHA-256")
        vertices = _int(item["vertices"], name + ".vertices", 3, 100_000)
        constraints = _int(item["constraints"], name + ".constraints", 1, 1_000_000)
        dt = _int(item["dt_ns"], name + ".dt_ns", 1_000, 1_000_000_000)
        steps = _int(item["steps"], name + ".steps", 2, MAX_SAMPLES + 10_000)
        warmup = _int(item["warmup_steps"], name + ".warmup_steps", 0, steps - 1)
        raw_samples = item["cpu_samples_us"]
        if type(raw_samples) is not list or not 1 <= len(raw_samples) <= MAX_SAMPLES or len(raw_samples) != steps - warmup:
            raise ReportError(name + ".cpu_samples_us: must match post-warmup step count and sample cap")
        samples = [_number(x, f"{name}.cpu_samples_us[{i}]") for i, x in enumerate(raw_samples)]
        samples_total += len(samples)
        if samples_total > 8_000:
            raise ReportError("total sample budget exceeded")
        checked.append({
            "engine": engine, "engine_version": version, "scenario_id": scenario, "seed": seed,
            "mesh_sha256": digest, "vertices": vertices, "constraints": constraints, "dt_ns": dt,
            "steps": steps, "warmup_steps": warmup, "cpu_samples_us": samples,
            "rss_peak_bytes": _int(item["rss_peak_bytes"], name + ".rss_peak_bytes", 1, 2**50),
            "penetration_count": _int(item["penetration_count"], name + ".penetration_count", 0, 2**31 - 1),
            "dropped_steps": _int(item["dropped_steps"], name + ".dropped_steps", 0, steps),
        })
    comparable = all(tuple(run[k] for k in IDENTITY) == tuple(checked[0][k] for k in IDENTITY) for run in checked[1:])
    summaries = []
    for run in checked:
        summaries.append({
            "engine": run["engine"], "engine_version": run["engine_version"],
            "cpu_median_us": _rank(run["cpu_samples_us"], .50),
            "cpu_p95_us": _rank(run["cpu_samples_us"], .95),
            "rss_peak_bytes": run["rss_peak_bytes"],
            "penetration_count": run["penetration_count"], "dropped_steps": run["dropped_steps"],
            "sample_count": len(run["cpu_samples_us"]),
        })
    ordering = sorted(range(len(summaries)), key=lambda i: (
        summaries[i]["cpu_p95_us"], summaries[i]["rss_peak_bytes"],
        summaries[i]["penetration_count"], summaries[i]["dropped_steps"], summaries[i]["engine"]
    )) if comparable else []
    normalized = []
    for run in checked:
        normalized.append({k: run[k] for k in IDENTITY} | {
            "engine": run["engine"], "engine_version": run["engine_version"],
            "cpu_samples_us": run["cpu_samples_us"], "rss_peak_bytes": run["rss_peak_bytes"],
            "penetration_count": run["penetration_count"], "dropped_steps": run["dropped_steps"],
        })
    fingerprint = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {
        "schema": "mojum.cloth-benchmark-comparison/1", "visibility": "PUBLIC_SYNTHETIC",
        "input_sha256": fingerprint, "comparable": comparable,
        "comparison_status": "same_workload_identity" if comparable else "not_comparable_identity_mismatch",
        "performance_order_by_p95_then_rss": [
            {"rank": rank + 1, **summaries[index]} for rank, index in enumerate(ordering)
        ],
        "candidates": summaries,
        "interpretation": "Informational measurements only; an ordering is not a solver winner or physics-equivalence claim." if comparable
            else "Workload identity differs; candidates are shown without ranking.",
        "limitations": [
            "No physics engine is executed or validated.",
            "Penetration and dropped-step counters are caller-reported.",
            "Matching identifiers do not prove equivalent material/contact/convergence semantics.",
            "CPU timing is machine- and environment-specific.",
        ],
    }


def load_report(path: str):
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise ReportError("input must be a regular non-symlink file")
    if target.stat().st_size > MAX_BYTES:
        raise ReportError("input exceeds 1 MiB")
    try:
        return json.loads(target.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReportError(f"invalid input: {type(exc).__name__}") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        result = json.dumps(analyze(load_report(args.input)), sort_keys=True, indent=2, allow_nan=False) + "\n"
        if args.output:
            output = Path(args.output)
            if output.exists() or output.is_symlink():
                raise ReportError("refusing to overwrite existing output")
            output.write_text(result, encoding="utf-8")
        else:
            sys.stdout.write(result)
    except (ReportError, OSError) as exc:
        print(f"cloth-benchmark-compare: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
