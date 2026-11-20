"""Minimal CPU-first typed graph contract validator (no execution or grants).

Provenance: original peer-4 bootstrap for mojum issue #1.
"""
from __future__ import annotations

from collections.abc import Mapping

VERSION = "mechanicus.universal-graph/1"
VISIBILITIES = frozenset({"PUBLIC", "PRIVATE", "RESTRICTED"})


class GraphContractError(ValueError):
    """Graph schema or edge typing is invalid."""


def validate_graph(graph: Mapping) -> None:
    """Validate a compact typed directed graph; return None on success.

    Nodes: {id, inputs: {port: type}, outputs: {port: type}, visibility}.
    Edges: {source, source_port, target, target_port}.
    This function does not authorize execution or alter the graph.
    """
    if not isinstance(graph, Mapping) or graph.get("schema") != VERSION:
        raise GraphContractError("unsupported graph schema")
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise GraphContractError("nodes and edges must be lists")
    index = {}
    for node in nodes:
        if not isinstance(node, Mapping):
            raise GraphContractError("node must be an object")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id or node_id in index:
            raise GraphContractError("node id missing or duplicated")
        if node.get("visibility") not in VISIBILITIES:
            raise GraphContractError("invalid visibility")
        for field in ("inputs", "outputs"):
            ports = node.get(field)
            if not isinstance(ports, Mapping) or any(
                not isinstance(k, str) or not k or not isinstance(v, str) or not v
                for k, v in ports.items()
            ):
                raise GraphContractError("invalid typed ports")
        index[node_id] = node
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise GraphContractError("edge must be an object")
        source, target = index.get(edge.get("source")), index.get(edge.get("target"))
        if source is None or target is None:
            raise GraphContractError("dangling edge")
        output = source["outputs"].get(edge.get("source_port"))
        input_type = target["inputs"].get(edge.get("target_port"))
        if output is None or input_type is None or output != input_type:
            raise GraphContractError("missing port or incompatible edge type")
