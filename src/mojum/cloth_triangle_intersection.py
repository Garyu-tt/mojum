"""Bounded exact triangle/triangle intersection audit for synthetic cloth meshes.

This is a geometric narrowphase utility, not a collision solver.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Iterable, Sequence

SCHEMA = "mojum.cloth-triangle-intersection/1"
MAX_VERTICES = 8192
MAX_TRIANGLES = 8192
MAX_PAIRS = 32768
_EPS = 1e-10


class TriangleIntersectionError(ValueError):
    """Invalid or over-budget triangle intersection input."""


@dataclass(frozen=True)
class TriangleIntersectionReport:
    schema: str
    candidate_count: int
    tested_count: int
    intersection_pairs: tuple[tuple[int, int], ...]
    skipped_adjacent: int
    digest_sha256: str


def _vec3(value: object, name: str) -> tuple[float, float, float]:
    if type(value) not in (tuple, list) or len(value) != 3:
        raise TriangleIntersectionError(f"{name} must be a native 3-vector")
    out = []
    for v in value:
        if type(v) not in (int, float) or not math.isfinite(v):
            raise TriangleIntersectionError(f"{name} coordinates must be finite native numbers")
        f = float(v)
        if not math.isfinite(f) or abs(f) > 1e9:
            raise TriangleIntersectionError(f"{name} coordinate outside supported range")
        out.append(f)
    return (out[0], out[1], out[2])


def _sub(a, b): return (a[0]-b[0], a[1]-b[1], a[2]-b[2])
def _dot(a, b): return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _area2(t):
    return _cross(_sub(t[1], t[0]), _sub(t[2], t[0]))


def _orient2(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def _on_segment2(a, b, p):
    return (min(a[0], b[0])-_EPS <= p[0] <= max(a[0], b[0])+_EPS and
            min(a[1], b[1])-_EPS <= p[1] <= max(a[1], b[1])+_EPS)


def _segments_intersect2(a, b, c, d):
    o1, o2, o3, o4 = _orient2(a,b,c), _orient2(a,b,d), _orient2(c,d,a), _orient2(c,d,b)
    if ((o1 > _EPS and o2 < -_EPS) or (o1 < -_EPS and o2 > _EPS)) and ((o3 > _EPS and o4 < -_EPS) or (o3 < -_EPS and o4 > _EPS)):
        return True
    return ((abs(o1) <= _EPS and _on_segment2(a,b,c)) or
            (abs(o2) <= _EPS and _on_segment2(a,b,d)) or
            (abs(o3) <= _EPS and _on_segment2(c,d,a)) or
            (abs(o4) <= _EPS and _on_segment2(c,d,b)))


def _point_in_tri2(p, tri):
    a,b,c = tri
    x,y,z = _orient2(a,b,p), _orient2(b,c,p), _orient2(c,a,p)
    return (x >= -_EPS and y >= -_EPS and z >= -_EPS) or (x <= _EPS and y <= _EPS and z <= _EPS)


def _project(tri, normal):
    axis = max(range(3), key=lambda i: abs(normal[i]))
    keep = tuple(i for i in range(3) if i != axis)
    return tuple((p[keep[0]], p[keep[1]]) for p in tri)


def _coplanar_intersects(a, b, normal):
    aa, bb = _project(a, normal), _project(b, normal)
    for i in range(3):
        for j in range(3):
            if _segments_intersect2(aa[i], aa[(i+1)%3], bb[j], bb[(j+1)%3]):
                return True
    return _point_in_tri2(aa[0], bb) or _point_in_tri2(bb[0], aa)


def _segment_hits_triangle(p0, p1, tri):
    direction = _sub(p1, p0)
    e1, e2 = _sub(tri[1], tri[0]), _sub(tri[2], tri[0])
    h = _cross(direction, e2)
    det = _dot(e1, h)
    if abs(det) <= _EPS:
        return False
    inv = 1.0 / det
    s = _sub(p0, tri[0])
    u = inv * _dot(s, h)
    if u < -_EPS or u > 1.0 + _EPS:
        return False
    q = _cross(s, e1)
    v = inv * _dot(direction, q)
    if v < -_EPS or u + v > 1.0 + _EPS:
        return False
    t = inv * _dot(e2, q)
    return -_EPS <= t <= 1.0 + _EPS


def _triangles_intersect(a, b):
    na, nb = _area2(a), _area2(b)
    da = [_dot(na, _sub(p, a[0])) for p in b]
    db = [_dot(nb, _sub(p, b[0])) for p in a]
    if (all(x > _EPS for x in da) or all(x < -_EPS for x in da) or
        all(x > _EPS for x in db) or all(x < -_EPS for x in db)):
        return False
    if all(abs(x) <= _EPS for x in da) and all(abs(x) <= _EPS for x in db):
        return _coplanar_intersects(a, b, na)
    for tri, other in ((a,b),(b,a)):
        for i in range(3):
            if _segment_hits_triangle(tri[i], tri[(i+1)%3], other):
                return True
    return False


def audit_triangle_intersections(vertices: Sequence[Sequence[float]],
                                 triangles: Sequence[Sequence[int]],
                                 candidate_pairs: Iterable[Sequence[int]]) -> TriangleIntersectionReport:
    """Test explicit candidate pairs; shared-vertex pairs are ignored as adjacent.

    Coordinates use caller-defined consistent units. Touching counts as intersection.
    """
    if type(vertices) not in (tuple, list) or not 3 <= len(vertices) <= MAX_VERTICES:
        raise TriangleIntersectionError("vertices must be a bounded native list/tuple")
    if type(triangles) not in (tuple, list) or not 1 <= len(triangles) <= MAX_TRIANGLES:
        raise TriangleIntersectionError("triangles must be a bounded native list/tuple")
    points = tuple(_vec3(v, "vertex") for v in vertices)
    faces = []
    for face in triangles:
        if type(face) not in (tuple, list) or len(face) != 3 or any(type(i) is not int for i in face):
            raise TriangleIntersectionError("each triangle must contain three native integer indices")
        if len(set(face)) != 3 or any(i < 0 or i >= len(points) for i in face):
            raise TriangleIntersectionError("triangle indices must be unique and in range")
        tri = tuple(points[i] for i in face)
        n = _area2(tri)
        if _dot(n,n) <= _EPS*_EPS:
            raise TriangleIntersectionError("degenerate triangle is not admissible")
        faces.append((tuple(face), tri))
    if type(candidate_pairs) not in (tuple, list):
        raise TriangleIntersectionError("candidate_pairs must be a native list/tuple")
    if len(candidate_pairs) > MAX_PAIRS:
        raise TriangleIntersectionError("candidate pair budget exceeded")
    pairs = []
    for pair in candidate_pairs:
        if type(pair) not in (tuple, list) or len(pair) != 2 or any(type(i) is not int for i in pair):
            raise TriangleIntersectionError("each candidate must be a pair of native triangle indices")
        i,j = pair
        if i < 0 or j < 0 or i >= len(faces) or j >= len(faces) or i == j:
            raise TriangleIntersectionError("candidate triangle indices out of range or identical")
        pairs.append((min(i,j), max(i,j)))
    if len(set(pairs)) != len(pairs):
        raise TriangleIntersectionError("duplicate candidate pair")
    pairs.sort()
    hits, skipped, tested = [], 0, 0
    for i,j in pairs:
        if set(faces[i][0]).intersection(faces[j][0]):
            skipped += 1
            continue
        tested += 1
        if _triangles_intersect(faces[i][1], faces[j][1]):
            hits.append((i,j))
    payload = {"schema": SCHEMA, "vertices": points, "triangles": [face for face, _ in faces],
               "candidate_pairs": pairs, "candidate_count": len(pairs), "tested_count": tested,
               "intersection_pairs": hits, "skipped_adjacent": skipped}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    return TriangleIntersectionReport(SCHEMA, len(pairs), tested, tuple(hits), skipped, digest)
