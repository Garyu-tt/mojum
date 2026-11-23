"""Bounded, deterministic, read-only cloth seam admission for synthetic meshes.

This module does not simulate cloth or mutate the caller's mesh.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

MAX_VERTICES = 100_000
MAX_PAIRS = 200_000


@dataclass(frozen=True)
class SeamReport:
    vertex_count: int
    pair_count: int
    group_count: int
    groups: tuple[tuple[int, ...], ...]


def audit_seams(vertex_count: int, pairs: object) -> SeamReport:
    """Validate a bounded iterable of unique undirected vertex stitch pairs.

    Each pair must connect distinct valid vertex indices. Components are
    returned in stable sorted order. Isolated non-seam vertices are excluded.
    """
    if type(vertex_count) is not int or not 0 <= vertex_count <= MAX_VERTICES:
        raise ValueError("vertex_count outside supported range")
    if type(pairs) not in (tuple, list):
        raise ValueError("pairs must be a list or tuple")
    if len(pairs) > MAX_PAIRS:
        raise ValueError("too many seam pairs")
    parent: dict[int, int] = {}

    def root(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    seen: set[tuple[int, int]] = set()
    for pair in pairs:
        if type(pair) not in (tuple, list) or len(pair) != 2:
            raise ValueError("each seam must have two indices")
        a, b = pair
        if (type(a) is not int or type(b) is not int
                or not 0 <= a < vertex_count or not 0 <= b < vertex_count):
            raise ValueError("invalid seam vertex index")
        if a == b:
            raise ValueError("self seam")
        edge = (min(a, b), max(a, b))
        if edge in seen:
            raise ValueError("duplicate seam (including reversed)")
        seen.add(edge)
        for node in edge:
            parent.setdefault(node, node)
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, list[int]] = defaultdict(list)
    for node in sorted(parent):
        groups[root(node)].append(node)
    ordered = tuple(sorted((tuple(nodes) for nodes in groups.values())))
    return SeamReport(vertex_count, len(seen), len(ordered), ordered)
