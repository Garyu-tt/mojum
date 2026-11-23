"""Offline, metadata-only resource admission for optional MuJoCo models.

The input is an assertion supplied by a caller, not a trusted MuJoCo readout.
This module deliberately imports no engine, opens no files and executes no model.
"""
from __future__ import annotations
import hashlib
import json
import re

METADATA_SCHEMA = "mojum.mujoco-model-metadata/1"
REPORT_SCHEMA = "mojum.mujoco-model-budget-report/1"
_MAX_COUNT = 10_000_000
_MAX_ENGINE_VERSION_LENGTH = 128
_COUNT_FIELDS = (
    "nbody", "njnt", "ngeom", "nsite", "nsensor", "nactuator", "ntendon",
    "nmesh", "nmeshvert", "nmeshface", "nflex", "nflexvert", "nflexedge",
    "nflexelem",
)
_REQUIRED_KEYS = frozenset(("schema", "engine_version", "model_sha256", *_COUNT_FIELDS))
_SHA256_RE = re.compile(r"[0-9a-f]{64}\\Z", re.ASCII)
_VERSION_RE = re.compile(r"[0-9]+\\.[0-9]+\\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\\Z", re.ASCII)

# Conservative admission profiles, not promises about speed, memory or a CPU.
_PROFILES = {
    "cpu_small": {
        "nbody": 64, "njnt": 32, "ngeom": 128, "nsite": 64, "nsensor": 32,
        "nactuator": 0, "ntendon": 16, "nmesh": 16, "nmeshvert": 12_000,
        "nmeshface": 24_000, "nflex": 0, "nflexvert": 0, "nflexedge": 0,
        "nflexelem": 0,
    },
    "cpu_standard": {
        "nbody": 256, "njnt": 128, "ngeom": 1_024, "nsite": 512, "nsensor": 128,
        "nactuator": 64, "ntendon": 128, "nmesh": 64, "nmeshvert": 100_000,
        "nmeshface": 200_000, "nflex": 8, "nflexvert": 50_000,
        "nflexedge": 150_000, "nflexelem": 100_000,
    },
}


class ModelBudgetError(ValueError):
    """Raised when caller-supplied model metadata is malformed."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")


def available_profiles() -> tuple[str, ...]:
    """Return stable names of built-in admission profiles."""
    return tuple(sorted(_PROFILES))


def _validate_metadata(metadata: object) -> dict:
    if type(metadata) is not dict:
        raise ModelBudgetError("metadata must be a built-in dict")
    # Check cardinality before materializing the key view. An exact built-in
    # dict can still contain an attacker-sized number of unexpected entries.
    if len(metadata) != len(_REQUIRED_KEYS):
        raise ModelBudgetError("metadata keys do not match schema")
    keys = list(metadata.keys())
    if any(type(key) is not str for key in keys):
        raise ModelBudgetError("metadata keys must be built-in strings")
    if set(keys) != _REQUIRED_KEYS:
        raise ModelBudgetError("metadata keys do not match schema")
    if type(metadata["schema"]) is not str or metadata["schema"] != METADATA_SCHEMA:
        raise ModelBudgetError("unsupported metadata schema")
    version = metadata["engine_version"]
    if (type(version) is not str
            or len(version) > _MAX_ENGINE_VERSION_LENGTH
            or _VERSION_RE.fullmatch(version) is None):
        raise ModelBudgetError(
            f"engine_version must be a semantic version of at most "
            f"{_MAX_ENGINE_VERSION_LENGTH} characters"
        )
    model_sha = metadata["model_sha256"]
    if type(model_sha) is not str or _SHA256_RE.fullmatch(model_sha) is None:
        raise ModelBudgetError("model_sha256 must be lowercase SHA-256 hex")

    observed = {}
    for field in _COUNT_FIELDS:
        value = metadata[field]
        if type(value) is not int or not 0 <= value <= _MAX_COUNT:
            raise ModelBudgetError(f"{field} must be an exact integer in [0, {_MAX_COUNT}]")
        observed[field] = value

    if observed["nbody"] < 1:
        raise ModelBudgetError("a MuJoCo model must declare at least one body")
    if observed["nmesh"] == 0 and (observed["nmeshvert"] or observed["nmeshface"]):
        raise ModelBudgetError("mesh vertex/face counts require at least one mesh")
    if observed["nmesh"] and (observed["nmeshvert"] == 0 or observed["nmeshface"] == 0):
        raise ModelBudgetError("declared meshes require vertices and faces")
    flex_counts = (observed["nflexvert"], observed["nflexedge"], observed["nflexelem"])
    if observed["nflex"] == 0 and any(flex_counts):
        raise ModelBudgetError("flex sub-counts require at least one flex object")
    if observed["nflex"] and not all(flex_counts):
        raise ModelBudgetError("declared flex objects require vertices, edges and elements")
    return observed


def audit_model_budget(metadata: object, profile: str = "cpu_small") -> dict:
    """Return a deterministic admission report for caller-supplied model counts.

    metadata must contain the exact keys described by METADATA_SCHEMA.
    The result is advisory: authorizes_execution is always false.
    """
    if type(profile) is not str or profile not in _PROFILES:
        raise ModelBudgetError("unknown model budget profile")
    observed = _validate_metadata(metadata)
    limits = dict(_PROFILES[profile])
    violations = [
        {"field": field, "observed": observed[field], "limit": limits[field]}
        for field in _COUNT_FIELDS
        if observed[field] > limits[field]
    ]
    report = {
        "schema": REPORT_SCHEMA,
        "engine_version": metadata["engine_version"],
        "model_sha256": metadata["model_sha256"],
        "profile": profile,
        "observed": observed,
        "limits": limits,
        "violations": violations,
        "status": "within_budget" if not violations else "over_budget",
        "admissible": not violations,
        "authorizes_execution": False,
        "requires_native_engine_validation": True,
    }
    report["report_sha256"] = hashlib.sha256(_canonical_json(report)).hexdigest()
    return report
