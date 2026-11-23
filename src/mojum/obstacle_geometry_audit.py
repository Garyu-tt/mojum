"""Bounded, read-only geometry audit for synthetic integer-mm rectangles.

This module does not interact with a running simulation.
"""
from __future__ import annotations

import hashlib
import json

SCHEMA = "mojum.obstacle-geometry-audit/1"
MAX_OBSTACLES = 128
MAX_COORD = 1_000_000
_EXPECTED_FIELDS = {"id", "x_min", "y_min", "x_max", "y_max"}


def audit_obstacles(obstacles: list[dict]) -> dict:
    """Return deterministic geometry metrics and positive-area overlaps.

    Rectangles use x_min, y_min, x_max, y_max in integer millimetres.
    Touching edges are not considered overlaps. Mapping keys are admitted
    before set comparisons so hostile str subclasses cannot run user code.
    """
    if type(obstacles) is not list or len(obstacles) > MAX_OBSTACLES:
        raise ValueError("obstacles must be a bounded native list")
    validated = []
    # A dict-backed index avoids constructing a set before record admission.
    ids: dict[str, None] = {}
    for item in obstacles:
        if type(item) is not dict:
            raise ValueError("invalid rectangle fields")
        # Bound cardinality before iterating attacker-controlled keys or
        # allocating a temporary set. Each rectangle has exactly five fields.
        if len(item) != len(_EXPECTED_FIELDS):
            raise ValueError("invalid rectangle field count")
        # Check key types/lengths before set()/equality: str subclasses can
        # override hashing/equality and execute arbitrary code during compares.
        if any(type(key) is not str or len(key) > 32 for key in item):
            raise ValueError("rectangle keys must be bounded exact native strings")
        if any(key not in _EXPECTED_FIELDS for key in item):
            raise ValueError("invalid rectangle fields")
        identifier = item["id"]
        if type(identifier) is not str or not (1 <= len(identifier) <= 64) or not all(c.isascii() and (c.isalnum() or c in "_-") for c in identifier):
            raise ValueError("invalid obstacle id")
        if identifier in ids:
            raise ValueError("duplicate obstacle id")
        ids[identifier] = None
        coords = tuple(item[k] for k in ("x_min", "y_min", "x_max", "y_max"))
        if any(type(v) is not int or abs(v) > MAX_COORD for v in coords):
            raise ValueError("coordinates must be bounded native integers")
        x0, y0, x1, y1 = coords
        if x0 >= x1 or y0 >= y1:
            raise ValueError("rectangle must have positive width and height")
        validated.append((identifier, x0, y0, x1, y1))
    validated.sort()
    metrics = [
        {"id": name, "area_mm2": (x1-x0)*(y1-y0), "perimeter_mm": 2*(x1-x0+y1-y0)}
        for name, x0, y0, x1, y1 in validated
    ]
    overlaps = []
    for i, (a, ax0, ay0, ax1, ay1) in enumerate(validated):
        for b, bx0, by0, bx1, by1 in validated[i+1:]:
            width = max(0, min(ax1, bx1)-max(ax0, bx0))
            height = max(0, min(ay1, by1)-max(ay0, by0))
            if width and height:
                overlaps.append({"a": a, "b": b, "area_mm2": width*height})
    payload = {"schema": SCHEMA, "metrics": metrics, "overlaps": overlaps}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {**payload, "sha256": hashlib.sha256(canonical.encode("ascii")).hexdigest()}
