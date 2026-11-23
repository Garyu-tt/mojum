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
        for name, value in self.as_dict().items():
            if type(value) is not int or value < 1:
                raise ResourceBudgetError(f"{name} must be a positive native integer")
        ceilings = (2_000_000, 12_000_000, 512, 512 * 1024 * 1024, 512, 2 * 1024**3)
        if any(v > cap for v, cap in zip(self.as_dict().values(), ceilings)):
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
    # frozen dataclasses can be mutated via object.__setattr__; revalidate
    # configured limits at this public trust boundary.
    limits.__post_init__()
    if type(inventory) is not dict:
        raise ResourceBudgetError("inventory must be a native dict")

    # Bound cardinality and key sizes before hashing, traversing, or serializing
    # caller-controlled inventory content.
    if len(inventory) != len(FIELDS) + 1:
        raise ResourceBudgetError("inventory fields must exactly match the versioned schema")
    for key in inventory:
        if type(key) is not str or len(key) > 32:
            raise ResourceBudgetError("inventory keys must be bounded native strings")
    if set(inventory) != {"schema", *FIELDS}:
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

    # Serialize only the now-admitted six-field projection, never the raw input.
    canonical = json.dumps(
        {"schema": SCHEMA, **counts}, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    if len(canonical) > MAX_INVENTORY_BYTES:
        raise ResourceBudgetError("inventory exceeds encoded size limit")

    estimate = sum(counts[name] * COST[name] for name in FIELDS)
    caps = {"vertex_count": limits.max_vertices, "index_count": limits.max_indices,
            "material_count": limits.max_materials, "texture_bytes": limits.max_texture_bytes,
            "joint_count": limits.max_joints, "estimated_bytes": limits.max_estimated_bytes}
    values = {**counts, "estimated_bytes": estimate}
    exceeded = tuple(k for k, v in values.items() if v > caps[k])
    warnings = tuple(k for k, v in values.items() if v > caps[k] * 0.8 and v <= caps[k])
    decision = "reject" if exceeded else ("warn" if warnings else "accept")
    return ResourceBudgetReport(SCHEMA, decision, estimate, hashlib.sha256(canonical).hexdigest(),
                                tuple(sorted(counts.items())), exceeded, warnings)
