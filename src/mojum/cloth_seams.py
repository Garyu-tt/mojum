"""Bounded offline seam-path admission for synthetic cloth panels."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re

_SCHEMA = "mojum.cloth-seams/1"
_MAX_VERTICES = 4096
_MAX_FACES = 8192
_MAX_SEAMS = 256
_MAX_PAIRS = 1024
_MAX_GAP = 0.25
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")


class ClothSeamError(ValueError):
    """Invalid seam geometry or an input outside the bounded contract."""


@dataclass(frozen=True, slots=True)
class SeamSummary:
    seam_id: str
    stitch_count: int
    max_gap_m: float
    total_gap_m: float


@dataclass(frozen=True, slots=True)
class SeamValidation:
    schema: str
    panel_count: int
    boundary_edge_count: int
    seam_count: int
    stitch_count: int
    max_gap_m: float
    seams: tuple[SeamSummary, ...]
    sha256: str


def _number(value: object, field: str) -> float:
    if type(value) not in (int, float):
        raise ClothSeamError(f"{field} must be a native int or float")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ClothSeamError(f"{field} is outside the numeric budget") from exc
    if not math.isfinite(result) or abs(result) > 1_000_000:
        raise ClothSeamError(f"{field} is non-finite or outside the coordinate budget")
    return result


def validate_seams(vertices: list, triangles: list, seams: list, *,
                   provenance: str, max_gap_m: int | float = 0.01) -> SeamValidation:
    """Validate ordered paired boundary paths joining disconnected mesh panels.

    provenance is an explicit caller assertion, not proof of data origin.
    Mesh coordinates are metres. Each seam's pairs are [[side_a, side_b], ...].
    """
    if type(provenance) is not str or provenance != "PUBLIC_SYNTHETIC":
        raise ClothSeamError("provenance must be explicitly PUBLIC_SYNTHETIC")
    gap_limit = _number(max_gap_m, "max_gap_m")
    if not 0 <= gap_limit <= _MAX_GAP:
        raise ClothSeamError("max_gap_m must be between 0 and 0.25 metres")
    if type(vertices) is not list or not 3 <= len(vertices) <= _MAX_VERTICES:
        raise ClothSeamError("vertices must contain 3..4096 native lists")
    if type(triangles) is not list or not 1 <= len(triangles) <= _MAX_FACES:
        raise ClothSeamError("triangles must contain 1..8192 native lists")
    if type(seams) is not list or not 1 <= len(seams) <= _MAX_SEAMS:
        raise ClothSeamError("seams must contain 1..256 native dictionaries")

    points = []
    for i, point in enumerate(vertices):
        if type(point) is not list or len(point) != 3:
            raise ClothSeamError(f"vertex {i} must be an xyz list")
        points.append(tuple(_number(x, f"vertices[{i}]") for x in point))

    parent = list(range(len(points)))
    rank = [0] * len(points)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        a, b = find(a), find(b)
        if a == b:
            return
        if rank[a] < rank[b]:
            a, b = b, a
        parent[b] = a
        if rank[a] == rank[b]:
            rank[a] += 1

    edges = {}
    used = set()
    faces_seen = set()
    for fi, face in enumerate(triangles):
        if type(face) is not list or len(face) != 3 or any(type(x) is not int for x in face):
            raise ClothSeamError(f"triangle {fi} must contain three native integer indices")
        a, b, c = face
        if min(a, b, c) < 0 or max(a, b, c) >= len(points) or len({a, b, c}) != 3:
            raise ClothSeamError(f"triangle {fi} has invalid indices")
        key = tuple(sorted(face))
        if key in faces_seen:
            raise ClothSeamError(f"triangle {fi} duplicates a face")
        faces_seen.add(key)
        p, q, r = points[a], points[b], points[c]
        u = (q[0]-p[0], q[1]-p[1], q[2]-p[2])
        v = (r[0]-p[0], r[1]-p[1], r[2]-p[2])
        cross = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        area2 = sum(x*x for x in cross)
        if not math.isfinite(area2) or area2 <= 1e-24:
            raise ClothSeamError(f"triangle {fi} is degenerate")
        union(a, b)
        union(b, c)
        used.update((a, b, c))
        for x, y in ((a, b), (b, c), (c, a)):
            edge = (x, y) if x < y else (y, x)
            edges[edge] = edges.get(edge, 0) + 1
            if edges[edge] > 2:
                raise ClothSeamError("mesh contains a non-manifold edge")

    if len(used) != len(points):
        raise ClothSeamError("mesh contains unreferenced vertices")
    boundary = {edge for edge, count in edges.items() if count == 1}
    if not boundary:
        raise ClothSeamError("mesh has no boundary edges")
    adjacency = {}
    for a, b in boundary:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    roots = {find(i) for i in range(len(points))}
    if len(roots) < 2:
        raise ClothSeamError("mesh must contain at least two disconnected panels")

    ids, endpoints, reports, all_gaps = set(), set(), [], []
    total_pairs = 0
    for si, seam in enumerate(seams):
        if type(seam) is not dict:
            raise ClothSeamError(f"seam {si} must be a native dictionary")
        # The schema always has two fields. Check cardinality before
        # copying or iterating caller-controlled dictionary keys.
        if len(seam) != 2:
            raise ClothSeamError(f"seam {si} must have exactly native-string keys id and pairs")
        keys = tuple(seam.keys())
        if any(type(key) is not str for key in keys) or set(keys) != {"id", "pairs"}:
            raise ClothSeamError(f"seam {si} must have exactly native-string keys id and pairs")
        sid, pairs = seam["id"], seam["pairs"]
        if type(sid) is not str or not _ID.fullmatch(sid) or sid in ids:
            raise ClothSeamError(f"seam {si} has an invalid or duplicate id")
        ids.add(sid)
        if type(pairs) is not list or len(pairs) < 2:
            raise ClothSeamError(f"seam {sid} needs at least two stitch pairs")
        total_pairs += len(pairs)
        if total_pairs > _MAX_PAIRS:
            raise ClothSeamError("total stitch-pair budget exceeded")
        side_a, side_b, gaps = [], [], []
        for pi, pair in enumerate(pairs):
            if type(pair) is not list or len(pair) != 2 or any(type(x) is not int for x in pair):
                raise ClothSeamError(f"seam {sid} pair {pi} must be two native indices")
            a, b = pair
            if a == b or min(a, b) < 0 or max(a, b) >= len(points):
                raise ClothSeamError(f"seam {sid} pair {pi} has invalid endpoints")
            if a not in adjacency or b not in adjacency:
                raise ClothSeamError(f"seam {sid} endpoints must be boundary vertices")
            if a in endpoints or b in endpoints or a in side_a or b in side_b:
                raise ClothSeamError("a boundary vertex may be used by only one stitch")
            endpoints.update((a, b))
            side_a.append(a)
            side_b.append(b)
            if find(a) == find(b):
                raise ClothSeamError("each stitch must join different mesh components")
            delta = tuple(points[a][i] - points[b][i] for i in range(3))
            distance = math.sqrt(sum(x*x for x in delta))
            if not math.isfinite(distance) or distance > gap_limit:
                raise ClothSeamError(f"seam {sid} exceeds max_gap_m")
            gaps.append(distance)
        component_pair = (find(side_a[0]), find(side_b[0]))
        if any((find(a), find(b)) != component_pair for a, b in zip(side_a, side_b)):
            raise ClothSeamError(f"seam {sid} changes panel pairing")
        for path in (side_a, side_b):
            if any(y not in adjacency.get(x, ()) for x, y in zip(path, path[1:])):
                raise ClothSeamError(f"seam {sid} is not a continuous boundary path")
        reports.append(SeamSummary(sid, len(pairs), max(gaps), math.fsum(gaps)))
        all_gaps.extend(gaps)

    payload = {"schema": _SCHEMA, "visibility": "PUBLIC_SYNTHETIC",
               "vertices": vertices, "triangles": triangles, "seams": seams,
               "max_gap_m": gap_limit}
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ClothSeamError("input is not canonical UTF-8 JSON") from exc
    if len(encoded) > 2_000_000:
        raise ClothSeamError("canonical input exceeds 2 MB")
    reports.sort(key=lambda report: report.seam_id)
    return SeamValidation(_SCHEMA, len(roots), len(boundary), len(reports),
                          total_pairs, max(all_gaps), tuple(reports),
                          hashlib.sha256(encoded).hexdigest())
