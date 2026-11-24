"""Conservative, bounded AABB broadphase for synthetic cloth self-intersection review."""
from dataclasses import dataclass
import hashlib
import json
import math

SCHEMA = "mojum.cloth.self-intersection-aabb/1"
PROVENANCE = "PUBLIC_SYNTHETIC"
MAX_VERTICES = 2048
MAX_TRIANGLES = 4096
MAX_COORDINATE_M = 1_000_000.0
MAX_PAIR_CHECKS = 200_000


@dataclass(frozen=True)
class SelfIntersectionAudit:
    schema: str
    provenance: str
    tested_pairs: int
    candidate_pairs: tuple[tuple[int, int], ...]
    input_sha256: str
    report_sha256: str


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and abs(value) <= MAX_COORDINATE_M
    except (OverflowError, TypeError, ValueError):
        return False


def audit_self_intersection_candidates(vertices, triangles, *, provenance,
                                       max_pair_checks=MAX_PAIR_CHECKS):
    """Return conservative AABB candidates, never exact collision claims."""
    if type(provenance) is not str or provenance != PROVENANCE:
        raise ValueError("PUBLIC_SYNTHETIC provenance required")
    if type(vertices) not in (tuple, list) or not 3 <= len(vertices) <= MAX_VERTICES:
        raise ValueError("invalid vertex collection")
    if type(triangles) not in (tuple, list) or not 1 <= len(triangles) <= MAX_TRIANGLES:
        raise ValueError("invalid triangle collection")
    if type(max_pair_checks) is not int or not 1 <= max_pair_checks <= MAX_PAIR_CHECKS:
        raise ValueError("invalid pair-check budget")
    possible_pairs = len(triangles) * (len(triangles) - 1) // 2
    if possible_pairs > max_pair_checks:
        raise ValueError("pair-check budget exceeded")

    points = []
    for vertex in vertices:
        if type(vertex) not in (tuple, list) or len(vertex) != 3 or not all(_number(v) for v in vertex):
            raise ValueError("invalid vertex")
        points.append(tuple(vertex))

    faces, seen = [], set()
    for face in triangles:
        if type(face) not in (tuple, list) or len(face) != 3:
            raise ValueError("invalid triangle")
        if any(type(i) is not int or i < 0 or i >= len(points) for i in face):
            raise ValueError("invalid triangle index")
        if len(set(face)) != 3:
            raise ValueError("degenerate triangle")
        key = tuple(sorted(face))
        if key in seen:
            raise ValueError("duplicate triangle")
        seen.add(key)
        faces.append(tuple(face))

    bounds = []
    for face in faces:
        tri = [points[i] for i in face]
        bounds.append(tuple((min(p[a] for p in tri), max(p[a] for p in tri)) for a in range(3)))

    candidates, tested = [], 0
    for left in range(len(faces)):
        for right in range(left + 1, len(faces)):
            if set(faces[left]).intersection(faces[right]):
                continue
            tested += 1
            if all(bounds[left][a][0] <= bounds[right][a][1]
                   and bounds[right][a][0] <= bounds[left][a][1] for a in range(3)):
                candidates.append((left, right))

    payload = {"schema": SCHEMA, "provenance": PROVENANCE, "vertices": points, "triangles": faces}
    input_sha = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    report = {"schema": SCHEMA, "input_sha256": input_sha, "tested_pairs": tested, "candidate_pairs": candidates}
    report_sha = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return SelfIntersectionAudit(SCHEMA, PROVENANCE, tested, tuple(candidates), input_sha, report_sha)
