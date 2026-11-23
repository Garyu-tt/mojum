"""Read-only information-flow ancestry witness for canonical graph-v2.

This is a *metadata audit*, not execution, permissions, declassification or
an authorization decision. Strict graph-v2 validation happens before any
lineage is reported, including v1 provenance and edge downgrade checks.
"""

from __future__ import annotations

import hashlib
import heapq
import json

from .graph import GraphError
from .graph_v2 import V2_SCHEMA, validate_v2

SCHEMA = "mojum.graph.flow-witness/1"
MAX_WITNESS_NODES = 256
MAX_NODE_ID_BYTES = 128
MAX_LINEAGE_PAIRS = 8192
MAX_REPORT_BYTES = 1024 * 1024


def _canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8", errors="strict")


def attest_flow(graph):
    """Return a deterministic synthetic-friendly, non-executable v2 DAG witness.

    Reject invalid or forged graph-v2 data using the canonical validator.
    Nodes and incoming edges appear in sorted ID/port order regardless of
    input traversal order. The roots of each node's ancestry are reported as
    source_nodes; ancestors excludes the node itself. No config, notes or
    private event payload is ever copied to the report.
    """
    # A small output-specific preflight prevents arbitrarily long IDs from
    # amplifying the ancestry report. The canonical graph itself is untouched.
    if type(graph) is not dict:
        raise GraphError("flow witness requires graph-v2 object")
    raw_nodes = graph.get("nodes")
    if type(raw_nodes) is not list or not 1 <= len(raw_nodes) <= MAX_WITNESS_NODES:
        raise GraphError("flow witness node budget exceeded")
    for node in raw_nodes:
        if type(node) is not dict or type(node.get("id")) is not str:
            raise GraphError("flow witness requires string node IDs")
        try:
            byte_length = len(node["id"].encode("utf-8", errors="strict"))
        except UnicodeError as exc:
            raise GraphError("flow witness node ID is not UTF-8") from exc
        if not 1 <= byte_length <= MAX_NODE_ID_BYTES:
            raise GraphError("flow witness node ID byte budget exceeded")

    audit = validate_v2(graph)
    if audit["schema"] != V2_SCHEMA:
        raise GraphError("unexpected graph-v2 validator response")
    nodes = {node["id"]: node for node in raw_nodes}
    incoming = {nid: [] for nid in nodes}
    outgoing = {nid: set() for nid in nodes}
    indegree = {nid: 0 for nid in nodes}
    for edge in graph["edges"]:
        src = edge["from"]
        dst = edge["to"]
        sid, did = src["node"], dst["node"]
        incoming[did].append((sid, src["port"], dst["port"]))
        if did not in outgoing[sid]:
            outgoing[sid].add(did)
            indegree[did] += 1

    ready = [nid for nid, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    ancestors = {}
    roots = {}
    visited = 0
    total_pairs = 0
    while ready:
        nid = heapq.heappop(ready)
        visited += 1
        parents = {edge[0] for edge in incoming[nid]}
        previous = set(parents)
        origin = set()
        for parent in parents:
            previous.update(ancestors[parent])
            origin.update(roots[parent])
        if not parents:
            origin.add(nid)
        total_pairs += len(previous)
        if total_pairs > MAX_LINEAGE_PAIRS:
            raise GraphError("flow witness lineage pair budget exceeded")
        ancestors[nid] = previous
        roots[nid] = origin
        for dst in sorted(outgoing[nid]):
            indegree[dst] -= 1
            if indegree[dst] == 0:
                heapq.heappush(ready, dst)
    # validate_v2 already rejects cycles, but never issue a partial witness
    # if a future validator implementation changes.
    if visited != len(nodes):
        raise GraphError("flow witness graph has a cycle")

    entries = []
    for nid in sorted(nodes):
        node = nodes[nid]
        entries.append({
            "id": nid,
            "kind": node["kind"],
            "declared_visibility": node["visibility"],
            "incoming": [
                {"from_node": sid, "from_port": source_port,
                 "to_port": dest_port}
                for sid, source_port, dest_port in sorted(
                    incoming[nid], key=lambda item: (item[0], item[1], item[2])
                )
            ],
            "ancestors": sorted(ancestors[nid]),
            "source_nodes": sorted(roots[nid]),
        })
    result = {
        "schema": SCHEMA,
        "graph_schema": V2_SCHEMA,
        "graph_sha256": audit["graph_sha256"],
        "origin_v1_sha256": audit["origin_sha256"],
        "node_count": len(nodes),
        "edge_count": len(graph["edges"]),
        "lineage_pairs": total_pairs,
        "execution": "NOT_SUPPORTED",
        "security": "labels-only-not-access-control",
        "nodes": entries,
    }
    try:
        payload = _canonical_bytes(result)
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise GraphError("flow witness is not canonical JSON") from exc
    if len(payload) > MAX_REPORT_BYTES:
        raise GraphError("flow witness report byte budget exceeded")
    result["witness_sha256"] = hashlib.sha256(payload).hexdigest()
    return result
