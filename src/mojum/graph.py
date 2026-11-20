"""Typed graph contract validator: no dynamically executable user code."""

from collections import defaultdict

SCHEMA = "mechanicus.universal-graph/1"

# Input/output port name -> type. Future plugin definitions require signed registration.
PORTS = {
    "genesis.seed": ({}, {"genome": "genome/1"}),
    "body.heartbeat": ({"genome": "genome/1"}, {"pulse": "heartbeat/1"}),
    "sensory.interoception": ({"pulse": "heartbeat/1"}, {"sensory": "sensory/1"}),
    "world.room": ({}, {"world": "world/1"}),
    "brain.placeholder": ({"sensory": "sensory/1", "world": "world/1"}, {"observation": "brain-observation/1"}),
    "memory.episode": ({"sensory": "sensory/1", "observation": "brain-observation/1"}, {"episode": "episode/1"}),
}


class GraphError(ValueError):
    pass


def validate(graph):
    if not isinstance(graph, dict) or graph.get("schema") != SCHEMA:
        raise GraphError("Unsupported or absent graph schema")
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not nodes or not isinstance(edges, list):
        raise GraphError("nodes must be nonempty list and edges must be list")
    if len(nodes) > 256 or len(edges) > 2048:
        raise GraphError("Graph exceeds safe bootstrap size")
    by_id = {}
    for n in nodes:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str) or not n["id"]:
            raise GraphError("Every node requires a nonempty string ID")
        if n["id"] in by_id:
            raise GraphError("Duplicate node ID: " + n["id"])
        kind = n.get("kind")
        # JSON graph kinds must be plain strings before dictionary lookup.
        # Unhashable input (list/dict) must fail as GraphError, not TypeError.
        if type(kind) is not str or kind not in PORTS:
            raise GraphError("Unsupported node kind: " + str(kind))
        if not isinstance(n.get("config"), dict) or not isinstance(n.get("notes"), dict):
            raise GraphError("Every node needs config and notes objects")
        if not n["notes"].get("purpose"):
            raise GraphError("Node notes.purpose is required")
        by_id[n["id"]] = n
    incoming = defaultdict(dict)
    adjacency = defaultdict(set)
    indegree = {name: 0 for name in by_id}
    for e in edges:
        try:
            src, dst = e["from"], e["to"]
            sid, sport, did, dport = src["node"], src["port"], dst["node"], dst["port"]
        except (TypeError, KeyError):
            raise GraphError("Malformed edge endpoint") from None
        if not all(isinstance(value, str) for value in (sid, sport, did, dport)):
            raise GraphError("Malformed edge endpoint")
        if sid not in by_id or did not in by_id:
            raise GraphError("Edge references absent node")
        if sport not in PORTS[by_id[sid]["kind"]][1] or dport not in PORTS[by_id[did]["kind"]][0]:
            raise GraphError("Edge references unknown port")
        if PORTS[by_id[sid]["kind"]][1][sport] != PORTS[by_id[did]["kind"]][0][dport]:
            raise GraphError("Mismatched typed ports")
        if dport in incoming[did]:
            raise GraphError("Input port connected more than once")
        incoming[did][dport] = (sid, sport)
        if did not in adjacency[sid]:
            adjacency[sid].add(did)
            indegree[did] += 1
    for nid, n in by_id.items():
        required_inputs = PORTS[n["kind"]][0]
        if required_inputs.keys() != incoming[nid].keys():
            raise GraphError("Missing connected input(s) at " + nid)
    ready = sorted(nid for nid, deg in indegree.items() if deg == 0)
    order = []
    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for dst in sorted(adjacency[nid]):
            indegree[dst] -= 1
            if indegree[dst] == 0:
                ready.append(dst)
                ready.sort()
    if len(order) != len(by_id):
        raise GraphError("Cycle detected: feedback requires explicit scheduler")
    return by_id, incoming, order
