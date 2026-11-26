"""Bounded, offline topology admission for synthetic cloth triangle proxies.

This is not a cloth solver or a GLB importer. A passed audit proves neither
self-collision safety nor physically correct material/rig anchoring.
"""

from dataclasses import dataclass
import hashlib
import json
import math

MAX_VERTICES = 4096
MAX_TRIANGLES = 8192
MAX_ABS_COORDINATE_M = 10000
MIN_DOUBLE_AREA_SQUARED = 1e-24


@dataclass(frozen=True)
class TopologyReport:
    schema: str
    vertex_count: int
    triangle_count: int
    edge_count: int
    boundary_edge_count: int
    boundary_loop_count: int
    component_count: int
    sha256: str


def _mesh_vertices(vertices):
    if type(vertices) not in (tuple, list) or not 3 <= len(vertices) <= MAX_VERTICES:
        raise ValueError("invalid vertex collection")
    canonical = []
    seen = set()
    for vertex in vertices:
        if type(vertex) not in (tuple, list) or len(vertex) != 3:
            raise ValueError("invalid vertex shape")
        coords = []
        for value in vertex:
            if type(value) not in (int, float) or abs(value) > MAX_ABS_COORDINATE_M:
                raise ValueError("invalid coordinate")
            try:
                if not math.isfinite(value):
                    raise ValueError("invalid coordinate")
                f = float(value)
            except (OverflowError, TypeError, ValueError) as exc:
                raise ValueError("invalid coordinate") from exc
            coords.append(0.0 if f == 0.0 else f)
        xyz = tuple(coords)
        if xyz in seen:
            raise ValueError("duplicate vertex position; weld geometry first")
        seen.add(xyz)
        canonical.append(xyz)
    return tuple(canonical)


def _mesh_triangles(triangles, vertices):
    if type(triangles) not in (tuple, list) or not 1 <= len(triangles) <= MAX_TRIANGLES:
        raise ValueError("invalid triangle collection")
    canonical = []
    seen = set()
    for tri in triangles:
        if (type(tri) not in (tuple, list) or len(tri) != 3
                or any(type(i) is not int or i < 0 or i >= len(vertices) for i in tri)):
            raise ValueError("invalid triangle indices")
        a, b, c = tri
        if len({a, b, c}) != 3:
            raise ValueError("repeated triangle vertex")
        key = tuple(sorted(tri))
        if key in seen:
            raise ValueError("duplicate triangle")
        seen.add(key)
        p, q, r = (vertices[i] for i in tri)
        ux, uy, uz = (q[i] - p[i] for i in range(3))
        vx, vy, vz = (r[i] - p[i] for i in range(3))
        cross = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        if sum(x * x for x in cross) <= MIN_DOUBLE_AREA_SQUARED:
            raise ValueError("zero or near-zero-area triangle")
        canonical.append((a, b, c))
    return tuple(canonical)


def audit_cloth_mesh(vertices, triangles, *, require_connected=True):
    """Validate a static synthetic triangle mesh; return reproducible statistics.

    Admits orientable 2-manifold triangulations with optional open boundaries.
    `require_connected=False` permits separate valid panels, still rejecting
    isolated vertices, bow-ties and non-manifold edges. Never runs physics.
    """
    if type(require_connected) is not bool:
        raise ValueError("require_connected must be bool")
    v = _mesh_vertices(vertices)
    t = _mesh_triangles(triangles, v)
    edges = {}
    links = [dict() for _ in v]
    parent = list(range(len(v)))
    used = set()

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b, c in t:
        used.update((a, b, c))
        parent[root(b)] = root(a)
        parent[root(c)] = root(a)
        for start, end in ((a, b), (b, c), (c, a)):
            key = (min(start, end), max(start, end))
            prior = edges.get(key)
            if prior is None:
                edges[key] = [1, (start, end)]
            else:
                if prior[0] >= 2:
                    raise ValueError("non-manifold edge (>2 incident faces)")
                if prior[1] == (start, end):
                    raise ValueError("inconsistent adjacent face winding")
                prior[0] = 2
        for center, n1, n2 in ((a, b, c), (b, c, a), (c, a, b)):
            links[center].setdefault(n1, set()).add(n2)
            links[center].setdefault(n2, set()).add(n1)

    if len(used) != len(v):
        raise ValueError("isolated vertex")
    boundary = [edge for edge, (incidence, _) in edges.items() if incidence == 1]
    boundary_neighbors = {}
    for a, b in boundary:
        boundary_neighbors.setdefault(a, set()).add(b)
        boundary_neighbors.setdefault(b, set()).add(a)
    if any(len(n) != 2 for n in boundary_neighbors.values()):
        raise ValueError("non-manifold boundary junction")

    # Vertex links must be a single ring (interior) or a single path
    # (boundary); an edge-incidence-only audit misses closed bow-ties.
    for center, graph in enumerate(links):
        if any(len(adj) > 2 for adj in graph.values()):
            raise ValueError("non-manifold vertex fan")
        visited = set()
        stack = [next(iter(graph))]
        while stack:
            n = stack.pop()
            if n not in visited:
                visited.add(n)
                stack.extend(graph[n] - visited)
        if len(visited) != len(graph):
            raise ValueError("disconnected vertex fan")
        end_count = sum(len(adj) == 1 for adj in graph.values())
        if center in boundary_neighbors:
            if end_count != 2:
                raise ValueError("invalid boundary vertex fan")
        elif end_count != 0:
            raise ValueError("invalid interior vertex fan")

    visited_boundary = set()
    loops = 0
    for start in boundary_neighbors:
        if start in visited_boundary:
            continue
        loops += 1
        stack = [start]
        while stack:
            n = stack.pop()
            if n not in visited_boundary:
                visited_boundary.add(n)
                stack.extend(boundary_neighbors[n] - visited_boundary)

    components = len({root(i) for i in used})
    if require_connected and components != 1:
        raise ValueError("disconnected cloth panels")
    packed = json.dumps({"schema": "mojum.cloth.topology/1", "vertices": v,
                         "triangles": t}, separators=(",", ":"), allow_nan=False)
    return TopologyReport("mojum.cloth.topology/1", len(v), len(t), len(edges),
                          len(boundary), loops, components,
                          hashlib.sha256(packed.encode("utf-8")).hexdigest())
