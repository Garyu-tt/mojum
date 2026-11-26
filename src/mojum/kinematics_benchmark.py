"""Deterministic admission contract for synthetic FK/IK benchmark results.

This module validates measurements supplied by an external benchmark runner. It
does not implement kinematics, physics, hardware sensing, or solver execution.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

SCHEMA = "mojum.kinematics-benchmark/1"
CLASSIFICATION = "PUBLIC_SYNTHETIC"
_MAX_ID = 96
_MAX_DOFS = 512
_MAX_SOLVER_TEXT = 96
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BenchmarkValidationError(ValueError):
    """Raised when a benchmark record is malformed or outside policy bounds."""


def _plain_int(value: Any, name: str, minimum: int = 0, maximum: int = 10**12) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise BenchmarkValidationError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _text(value: Any, name: str, limit: int = _MAX_ID) -> str:
    if type(value) is not str or not value or len(value) > limit or value.strip() != value:
        raise BenchmarkValidationError(f"{name} must be a non-empty trimmed string of at most {limit} chars")
    if any(ord(ch) < 32 for ch in value):
        raise BenchmarkValidationError(f"{name} contains control characters")
    return value


def _finite_number(value: Any, name: str, minimum: float = 0.0, maximum: float = 1e15) -> float:
    if type(value) not in (int, float):
        raise BenchmarkValidationError(f"{name} must be a native int or float")
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        raise BenchmarkValidationError(f"{name} must be finite and in [{minimum}, {maximum}]") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise BenchmarkValidationError(f"{name} must be finite and in [{minimum}, {maximum}]")
    return number


def _mapping(value: Any, name: str, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or len(value) != len(keys):
        raise BenchmarkValidationError(f"{name} must be a bounded plain dict with exactly: {', '.join(sorted(keys))}")
    supplied = tuple(value.keys())
    maximum_key_length = max(map(len, keys))
    if any(type(key) is not str or len(key) > maximum_key_length for key in supplied):
        raise BenchmarkValidationError(f"{name} keys must be bounded native strings")
    if set(supplied) != keys:
        raise BenchmarkValidationError(f"{name} must be a plain dict with exactly: {', '.join(sorted(keys))}")
    return value


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def build_benchmark_report(
    *,
    rig_id: str,
    rig_revision: str,
    dof_count: int,
    solver_name: str,
    solver_version: str,
    solver_license: str,
    case_id: str,
    seed: int,
    fk_samples: int,
    fk_max_position_error_um: int,
    ik_attempts: int,
    ik_converged: int,
    ik_max_residual_um: int,
    joint_limit_violations: int,
    contact_events: int,
    cpu_p50_us: int,
    cpu_p95_us: int,
    max_rss_kib: int,
) -> dict[str, Any]:
    """Validate caller measurements and return a detached, digest-sealed report.

    All numeric inputs are integers with explicit units. The receipt is an
    evidence container only: callers remain responsible for measurement quality.
    """
    rig_id = _text(rig_id, "rig_id")
    rig_revision = _text(rig_revision, "rig_revision")
    case_id = _text(case_id, "case_id")
    solver = {
        "name": _text(solver_name, "solver_name", _MAX_SOLVER_TEXT),
        "version": _text(solver_version, "solver_version", _MAX_SOLVER_TEXT),
        "license": _text(solver_license, "solver_license", _MAX_SOLVER_TEXT),
    }
    dof_count = _plain_int(dof_count, "dof_count", 1, _MAX_DOFS)
    seed = _plain_int(seed, "seed", 0, 2**32 - 1)
    fk_samples = _plain_int(fk_samples, "fk_samples", 1, 1_000_000)
    fk_max_position_error_um = _plain_int(fk_max_position_error_um, "fk_max_position_error_um", 0, 10**9)
    ik_attempts = _plain_int(ik_attempts, "ik_attempts", 1, 1_000_000)
    ik_converged = _plain_int(ik_converged, "ik_converged", 0, ik_attempts)
    ik_max_residual_um = _plain_int(ik_max_residual_um, "ik_max_residual_um", 0, 10**9)
    joint_limit_violations = _plain_int(joint_limit_violations, "joint_limit_violations", 0, 10**9)
    contact_events = _plain_int(contact_events, "contact_events", 0, 10**9)
    cpu_p50_us = _plain_int(cpu_p50_us, "cpu_p50_us", 0, 10**12)
    cpu_p95_us = _plain_int(cpu_p95_us, "cpu_p95_us", cpu_p50_us, 10**12)
    max_rss_kib = _plain_int(max_rss_kib, "max_rss_kib", 1, 2**40)

    payload = {
        "schema": SCHEMA,
        "classification": CLASSIFICATION,
        "rig": {"id": rig_id, "revision": rig_revision, "dof_count": dof_count},
        "solver": solver,
        "case": {"id": case_id, "seed": seed},
        "fk": {"samples": fk_samples, "max_position_error_um": fk_max_position_error_um},
        "ik": {
            "attempts": ik_attempts,
            "converged": ik_converged,
            "max_residual_um": ik_max_residual_um,
            "joint_limit_violations": joint_limit_violations,
        },
        "contacts": {"events": contact_events, "authority": "SIMULATED_ONLY"},
        "resources": {"cpu_p50_us": cpu_p50_us, "cpu_p95_us": cpu_p95_us, "max_rss_kib": max_rss_kib},
    }
    return {**payload, "sha256": _canonical_digest(payload)}


def verify_benchmark_report(report: Any) -> bool:
    """Fail-closed verification of a report previously produced by this module."""
    keys = {"schema", "classification", "rig", "solver", "case", "fk", "ik", "contacts", "resources", "sha256"}
    report = _mapping(report, "report", keys)
    if type(report["schema"]) is not str or report["schema"] != SCHEMA:
        raise BenchmarkValidationError("unsupported schema")
    if type(report["classification"]) is not str or report["classification"] != CLASSIFICATION:
        raise BenchmarkValidationError("unsupported classification")
    # Validate every nested mapping before canonical serialization/hashing.
    rig = _mapping(report["rig"], "rig", {"id", "revision", "dof_count"})
    solver = _mapping(report["solver"], "solver", {"name", "version", "license"})
    case = _mapping(report["case"], "case", {"id", "seed"})
    fk = _mapping(report["fk"], "fk", {"samples", "max_position_error_um"})
    ik = _mapping(report["ik"], "ik", {"attempts", "converged", "max_residual_um", "joint_limit_violations"})
    contacts = _mapping(report["contacts"], "contacts", {"events", "authority"})
    resources = _mapping(report["resources"], "resources", {"cpu_p50_us", "cpu_p95_us", "max_rss_kib"})
    if type(contacts["authority"]) is not str or contacts["authority"] != "SIMULATED_ONLY":
        raise BenchmarkValidationError("unsupported contact authority")
    digest = report["sha256"]
    if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
        raise BenchmarkValidationError("sha256 must be 64 lowercase hexadecimal characters")
    payload = {key: value for key, value in report.items() if key != "sha256"}
    # Validate values and bounds before canonical serialization. A digest must
    # never force JSON work on unbounded caller-controlled strings or numbers.
    rebuilt = build_benchmark_report(
        rig_id=rig["id"], rig_revision=rig["revision"], dof_count=rig["dof_count"],
        solver_name=solver["name"], solver_version=solver["version"], solver_license=solver["license"],
        case_id=case["id"], seed=case["seed"], fk_samples=fk["samples"],
        fk_max_position_error_um=fk["max_position_error_um"], ik_attempts=ik["attempts"],
        ik_converged=ik["converged"], ik_max_residual_um=ik["max_residual_um"],
        joint_limit_violations=ik["joint_limit_violations"], contact_events=contacts["events"],
        cpu_p50_us=resources["cpu_p50_us"], cpu_p95_us=resources["cpu_p95_us"],
        max_rss_kib=resources["max_rss_kib"],
    )
    if rebuilt["sha256"] != digest:
        raise BenchmarkValidationError("report semantic validation failed")
    return True
