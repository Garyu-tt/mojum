"""Deterministic metadata-only surface map for synthetic point-room observations.

This module does not alter collision or movement physics. Coordinates and
coefficients use integer milli-units to keep receipts reproducible.
"""
from dataclasses import dataclass
import hashlib
import json

SCHEMA = "mojum.world.surface/1"
MAX_PATCHES = 256
MAX_ROOM_MM = 10_000_000
MAX_COEFFICIENT_MILLI = 3_000


def _native_int(value, low, high):
    return type(value) is int and low <= value <= high


def _token(value):
    return (type(value) is str and 1 <= len(value) <= 48
            and value.isascii()
            and all(c.isalnum() or c in "._-" for c in value))


@dataclass(frozen=True)
class SurfacePatch:
    patch_id: str
    material_id: str
    x_mm: int
    y_mm: int
    width_mm: int
    height_mm: int
    friction_milli: int
    restitution_milli: int
    priority: int = 0

    def __post_init__(self):
        if not _token(self.patch_id) or not _token(self.material_id):
            raise ValueError("invalid patch or material identifier")
        if not (_native_int(self.x_mm, 0, MAX_ROOM_MM)
                and _native_int(self.y_mm, 0, MAX_ROOM_MM)
                and _native_int(self.width_mm, 1, MAX_ROOM_MM)
                and _native_int(self.height_mm, 1, MAX_ROOM_MM)):
            raise ValueError("invalid patch geometry")
        if not (_native_int(self.friction_milli, 0, MAX_COEFFICIENT_MILLI)
                and _native_int(self.restitution_milli, 0, 1000)
                and _native_int(self.priority, -1000, 1000)):
            raise ValueError("invalid surface coefficients or priority")


@dataclass(frozen=True)
class SurfaceMap:
    room_revision: str
    room_width_mm: int
    room_height_mm: int
    patches: tuple[SurfacePatch, ...]
    schema: str = SCHEMA

    def __post_init__(self):
        # Validate exact type before equality: str subclasses may override hooks.
        if type(self.schema) is not str or self.schema != SCHEMA:
            raise ValueError("unsupported surface schema")
        if (type(self.room_revision) is not str or len(self.room_revision) != 64
                or any(c not in "0123456789abcdef" for c in self.room_revision)):
            raise ValueError("invalid room revision")
        if not (_native_int(self.room_width_mm, 1, MAX_ROOM_MM)
                and _native_int(self.room_height_mm, 1, MAX_ROOM_MM)):
            raise ValueError("invalid room dimensions")
        if type(self.patches) not in (tuple, list) or len(self.patches) > MAX_PATCHES:
            raise ValueError("invalid patch collection")
        detached = []
        seen = set()
        for patch in self.patches:
            if type(patch) is not SurfacePatch:
                raise ValueError("patch must be a native SurfacePatch")
            if patch.patch_id in seen:
                raise ValueError("duplicate patch identifier")
            seen.add(patch.patch_id)
            if (patch.x_mm + patch.width_mm > self.room_width_mm
                    or patch.y_mm + patch.height_mm > self.room_height_mm):
                raise ValueError("patch outside room")
            detached.append(patch)
        object.__setattr__(self, "patches", tuple(detached))


def _canonical_map(surface_map):
    return {
        "schema": surface_map.schema,
        "room_revision": surface_map.room_revision,
        "room_width_mm": surface_map.room_width_mm,
        "room_height_mm": surface_map.room_height_mm,
        "patches": [
            [p.patch_id, p.material_id, p.x_mm, p.y_mm, p.width_mm,
             p.height_mm, p.friction_milli, p.restitution_milli, p.priority]
            for p in sorted(surface_map.patches, key=lambda p: p.patch_id)
        ],
    }


def surface_map_digest(surface_map):
    if type(surface_map) is not SurfaceMap:
        raise ValueError("invalid surface map")
    raw = json.dumps(_canonical_map(surface_map), sort_keys=True,
                     separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def observe_surface(surface_map, *, x_mm, y_mm, tick, expected_room_revision):
    """Return the highest-priority containing patch, or the explicit default.

    Patch rectangles are half-open [x, x+w) × [y, y+h). Equal-priority
    overlaps resolve by lexicographically smallest patch_id.
    """
    if type(surface_map) is not SurfaceMap:
        raise ValueError("invalid surface map")
    if (type(expected_room_revision) is not str
            or expected_room_revision != surface_map.room_revision):
        raise ValueError("stale room revision")
    if not _native_int(tick, 0, 2**63 - 1):
        raise ValueError("invalid logical tick")
    if not (_native_int(x_mm, 0, surface_map.room_width_mm)
            and _native_int(y_mm, 0, surface_map.room_height_mm)):
        raise ValueError("sample outside room")
    matches = [
        p for p in surface_map.patches
        if p.x_mm <= x_mm < p.x_mm + p.width_mm
        and p.y_mm <= y_mm < p.y_mm + p.height_mm
    ]
    chosen = min(matches, key=lambda p: (-p.priority, p.patch_id)) if matches else None
    result = {
        "schema": SCHEMA,
        "room_revision": surface_map.room_revision,
        "tick": tick,
        "position_mm": [x_mm, y_mm],
        "patch_id": chosen.patch_id if chosen else None,
        "material_id": chosen.material_id if chosen else "default",
        "friction_milli": chosen.friction_milli if chosen else 500,
        "restitution_milli": chosen.restitution_milli if chosen else 0,
        "map_sha256": surface_map_digest(surface_map),
        "observation_only": True,
        "physical_contact": False,
    }
    return result


__all__ = ["SCHEMA", "SurfacePatch", "SurfaceMap",
           "surface_map_digest", "observe_surface"]
