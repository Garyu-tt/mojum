"""Bounded offline geometric deformation audit for synthetic cloth meshes.

This measures a supplied rest/current mesh pair. It does not simulate cloth,
infer contact, or establish physical correctness or solver equivalence.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

SCHEMA = "mojum.cloth.deformation-audit/1"
PUBLIC_VISIBILITY = "PUBLIC_SYNTHETIC"
MAX_VERTICES = 4096
MAX_TRIANGLES = 8192
MAX_ABS_COORDINATE_M = 10_000.0
MIN_REST_EDGE_M = 1e-9
MIN_REST_AREA_M2 = 1e-18
MAX_UNIQUE_EDGES = MAX_TRIANGLES * 3


class DeformationAuditError(ValueError):
    """Invalid or over-budget synthetic cloth geometry."""


@dataclass(frozen=True)
class DeformationReport:
    schema: str
    visibility: str
    vertex_count: int
    triangle_count: int
    unique_edge_count: int
    edge_ratio_min: float
    edge_ratio_median: float
    edge_ratio_p95: float
    edge_ratio_max: float
    edge_abs_strain_p95: float
    edge_abs_strain_max: float
    area_ratio_min: float
    area_ratio_median: float
    area_ratio_p95: float
    area_ratio_max: float
    collapsed_triangle_count: int
    orientation_reversal_count: int
    input_sha256: str


def _sequence(value, name, low, high):
    if type(value) not in (tuple, list) or not low <= len(value) <= high:
        raise DeformationAuditError(
            f"{name} must be a list/tuple with {low}..{high} entries"
        )
    return value


def _vertices(value, name):
    raw = _sequence(value, name, 3, MAX_VERTICES)
    result = []
    for index, vertex in enumerate(raw):
        if type(vertex) not in (tuple, list) or len(vertex) != 3:
            raise DeformationAuditError(f"{name}[{index}] must contain xyz")
        coords = []
        for axis, item in enumerate(vertex):
            if type(item) not in (int, float):
                raise DeformationAuditError(
                    f"{name}[{index}][{axis}] must be a native number"
                )
            try:
                number = float(item)
            except (OverflowError, ValueError):
                raise DeformationAuditError("coordinate out of range") from None
            if not math.isfinite(number) or abs(number) > MAX_ABS_COORDINATE_M:
                raise DeformationAuditError("coordinate must be finite and within +/-10000 m")
            coords.append(0.0 if number == 0.0 else number)
        result.append(tuple(coords))
    return tuple(result)


def _triangles(value, vertex_count):
    raw = _sequence(value, "triangles", 1, MAX_TRIANGLES)
    result = []
    seen = set()
    for index, triangle in enumerate(raw):
        if type(triangle) not in (tuple, list) or len(triangle) != 3:
            raise DeformationAuditError(f"triangles[{index}] must have three indices")
        if any(type(item) is not int or not 0 <= item < vertex_count for item in triangle):
            raise DeformationAuditError(f"triangles[{index}] has an invalid vertex index")
        a, b, c = triangle
        if len({a, b, c}) != 3:
            raise DeformationAuditError(f"triangles[{index}] repeats a vertex")
        identity = tuple(sorted((a, b, c)))
        if identity in seen:
            raise DeformationAuditError("duplicate triangle")
        seen.add(identity)
        result.append((a, b, c))
    return tuple(result)


def _cross_and_area(a, b, c):
    ux, uy, uz = (b[i] - a[i] for i in range(3))
    vx, vy, vz = (c[i] - a[i] for i in range(3))
    normal = (
        uy * vz - uz * vy,
        uz * vx - ux * vz,
        ux * vy - uy * vx,
    )
    return normal, math.hypot(*normal) * 0.5


def _percentile(values, fraction):
    ordered = sorted(values)
    # Nearest-rank percentile: rank = ceil(p * n), one-based.
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def audit_deformation(
    rest_vertices,
    current_vertices,
    triangles,
    *,
    visibility=PUBLIC_VISIBILITY,
):
    """Return deterministic geometric metrics for a synthetic rest/current mesh.

    All coordinates are metres. Each undirected triangle edge is measured once.
    Rest edges and rest triangle areas must be non-degenerate; collapsed current
    triangles are retained as zero-area observations and counted explicitly.
    Orientation reversals compare each current face normal with its rest normal.
    A reversal is a geometric warning, not proof of an invalid physical state:
    large folds can rotate a face normal by more than 90 degrees.

    Input is bounded to 4,096 vertices and 8,192 triangles. No coordinates are
    included in the returned report; input identity is a SHA-256 digest.
    """
    if type(visibility) is not str or visibility != PUBLIC_VISIBILITY:
        raise DeformationAuditError("only PUBLIC_SYNTHETIC input is accepted")
    rest = _vertices(rest_vertices, "rest_vertices")
    current = _vertices(current_vertices, "current_vertices")
    if len(current) != len(rest):
        raise DeformationAuditError("rest/current vertex counts differ")
    faces = _triangles(triangles, len(rest))

    edges = set()
    for a, b, c in faces:
        edges.add((min(a, b), max(a, b)))
        edges.add((min(b, c), max(b, c)))
        edges.add((min(c, a), max(c, a)))
        if len(edges) > MAX_UNIQUE_EDGES:
            raise DeformationAuditError("unique edge budget exceeded")

    edge_ratios = []
    edge_strains = []
    for a, b in sorted(edges):
        rest_length = math.dist(rest[a], rest[b])
        if not math.isfinite(rest_length) or rest_length < MIN_REST_EDGE_M:
            raise DeformationAuditError("rest mesh contains a degenerate edge")
        current_length = math.dist(current[a], current[b])
        ratio = current_length / rest_length
        if not math.isfinite(ratio):
            raise DeformationAuditError("current edge ratio is not finite")
        edge_ratios.append(ratio)
        edge_strains.append(abs(ratio - 1.0))

    area_ratios = []
    collapsed = 0
    reversals = 0
    for a, b, c in faces:
        rest_normal, rest_area = _cross_and_area(rest[a], rest[b], rest[c])
        if not math.isfinite(rest_area) or rest_area < MIN_REST_AREA_M2:
            raise DeformationAuditError("rest mesh contains a degenerate triangle")
        current_normal, current_area = _cross_and_area(current[a], current[b], current[c])
        if not math.isfinite(current_area):
            raise DeformationAuditError("current triangle area is not finite")
        ratio = current_area / rest_area
        if not math.isfinite(ratio):
            raise DeformationAuditError("current area ratio is not finite")
        area_ratios.append(ratio)
        if current_area < MIN_REST_AREA_M2:
            collapsed += 1
        dot = sum(rest_normal[i] * current_normal[i] for i in range(3))
        if dot < 0.0:
            reversals += 1

    canonical = {
        "schema": SCHEMA,
        "visibility": PUBLIC_VISIBILITY,
        "rest_vertices": rest,
        "current_vertices": current,
        "triangles": faces,
    }
    try:
        wire = json.dumps(canonical, sort_keys=True, ensure_ascii=True,
                          separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError, OverflowError) as exc:
        raise DeformationAuditError("mesh cannot be canonically encoded") from exc

    return DeformationReport(
        schema=SCHEMA,
        visibility=PUBLIC_VISIBILITY,
        vertex_count=len(rest),
        triangle_count=len(faces),
        unique_edge_count=len(edges),
        edge_ratio_min=min(edge_ratios),
        edge_ratio_median=_median(edge_ratios),
        edge_ratio_p95=_percentile(edge_ratios, 0.95),
        edge_ratio_max=max(edge_ratios),
        edge_abs_strain_p95=_percentile(edge_strains, 0.95),
        edge_abs_strain_max=max(edge_strains),
        area_ratio_min=min(area_ratios),
        area_ratio_median=_median(area_ratios),
        area_ratio_p95=_percentile(area_ratios, 0.95),
        area_ratio_max=max(area_ratios),
        collapsed_triangle_count=collapsed,
        orientation_reversal_count=reversals,
        input_sha256=hashlib.sha256(wire).hexdigest(),
    )
