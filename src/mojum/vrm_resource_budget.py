"""Offline stdlib resource preflight for optional VRM viewers."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json

SCHEMA = "mojum.vrm-resource-budget/1"
MAX_INVENTORY_BYTES = 16384
HARD = {"vertex_count": 2_000_000, "index_count": 12_000_000, "material_count": 512,
        "texture_bytes": 512 * 1024 * 1024, "joint_count": 512}
COST = {"vertex_count": 32, "index_count": 4, "material_count": 256,
        "texture_bytes": 1, "joint_count": 256}
FIELDS = ("vertex_count", "index_count", "material_count", "texture_bytes", "joint_count")
EXPECTED_KEYS = frozenset(("schema", *FIELDS))


class ResourceBudgetError(ValueError):
    """Invalid or unsafe resource inventory."""


@dataclass(frozen=True)
class ResourceLimits:
    max_vertices: int = 250_000
    max_indices: int = 1_500_000
    max_materials: int = 64
    max_texture_bytes: int = 64 * 1024 * 1024
    max_joints: int = 256
    max_estimated_bytes: int = 96 * 1024 * 1024

    def __post_init__(self):
        values = self.as_dict()
        for name, value in values.items():
            if type(value) is not int or value < 1:
                raise ResourceBudgetError(f"{name} must be a positive native integer")
        ceilings = (2_000_000, 12_000_000, 512, 512 * 1024 * 1024, 512, 2 * 1024**3)
        if any(v > cap for v, cap in zip(values.values(), ceilings)):
            raise ResourceBudgetError("configured limit exceeds hard safety ceiling")

    def as_dict(self):
        return {"max_vertices": self.max_vertices, "max_indices": self.max_indices,
                "max_materials": self.max_materials, "max_texture_bytes": self.max_texture_bytes,
                "max_joints": self.max_joints, "max_estimated_bytes": self.max_estimated_bytes}


@dataclass(frozen=True)
class ResourceBudgetReport:
    schema: str
    decision: str
    estimated_bytes: int
    inventory_sha256: str
    counts: tuple
    exceeded: tuple
    warnings: tuple

    def to_dict(self):
        return {"schema": self.schema, "decision": self.decision,
                "estimated_bytes": self.estimated_bytes, "inventory_sha256": self.inventory_sha256,
                "counts": dict(self.counts), "exceeded": list(self.exceeded),
                "warnings": list(self.warnings)}


def assess_vrm_resource_budget(inventory, limits=None):
    """Assess declared PUBLIC_SYNTHETIC metadata; never parses or loads an asset."""
    if limits is None:
        limits = ResourceLimits()
    if type(limits) is not ResourceLimits:
        raise ResourceBudgetError("limits must be a ResourceLimits instance")
    limits.__post_init__()
    if type(inventory) is not dict:
        raise ResourceBudgetError("inventory must be a native dict")
    # O(1) cardinality gate before iterating caller-controlled keys.
    if len(inventory) != len(EXPECTED_KEYS):
        raise ResourceBudgetError("inventory fields must exactly match the versioned schema")
    # Check exact key primitives and short lengths before hashing or comparing
    # caller-controlled keys in a set operation.
    for key in inventory:
        if type(key) is not str or not key or len(key) > 32:
            raise ResourceBudgetError("inventory field names must be bounded native strings")
    if set(inventory) != EXPECTED_KEYS:
        raise ResourceBudgetError("inventory fields must exactly match the versioned schema")
    if type(inventory["schema"]) is not str or inventory["schema"] != SCHEMA:
        raise ResourceBudgetError("unsupported inventory schema")
    counts = {}
    for field in FIELDS:
        value = inventory[field]
        if type(value) is not int or value < 0 or value > HARD[field]:
            raise ResourceBudgetError(f"{field} must be a bounded non-negative native integer")
        counts[field] = value
    if counts["index_count"] % 3:
        raise ResourceBudgetError("index_count must describe complete triangles")
    canonical = json.dumps({"schema": SCHEMA, **counts}, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(canonical) > MAX_INVENTORY_BYTES:
        raise ResourceBudgetError("inventory exceeds encoded size limit")
    estimate = sum(counts[name] * COST[name] for name in FIELDS)
    limit_values = limits.as_dict()
    caps = {"vertex_count": limit_values["max_vertices"],
            "index_count": limit_values["max_indices"],
            "material_count": limit_values["max_materials"],
            "texture_bytes": limit_values["max_texture_bytes"],
            "joint_count": limit_values["max_joints"],
            "estimated_bytes": limit_values["max_estimated_bytes"]}
    values = {**counts, "estimated_bytes": estimate}
    exceeded = tuple(k for k, v in values.items() if v > caps[k])
    warnings = tuple(k for k, v in values.items() if v > caps[k] * 0.8 and v <= caps[k])
    decision = "reject" if exceeded else ("warn" if warnings else "accept")
    return ResourceBudgetReport(SCHEMA, decision, estimate, hashlib.sha256(canonical).hexdigest(),
                                tuple(sorted(counts.items())), exceeded, warnings)
