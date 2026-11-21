"""Typed graph contract validator: no dynamically executable user code."""

from collections import defaultdict
import json

SCHEMA = "mechanicus.universal-graph/1"
MAX_JSON_DEPTH = 256

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


def _unique_json_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise GraphError(f"Duplicate graph JSON key: {key!r}")
        obj[key] = value
    return obj


def _check_json_depth(raw):
    """Reject deeply nested JSON before invoking the recursive decoder.

    ``json.loads`` has no portable public depth limit.  A deeply nested graph
    can otherwise raise ``RecursionError`` on one Python version and be
    accepted on another.  Brackets inside JSON strings are ignored; the JSON
    decoder remains responsible for all other syntax validation.
    """
    depth = 0
    in_string = False
    escaped = False
    for char in raw:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise GraphError("Graph JSON nesting depth exceeded")
        elif char in "]}" and depth:
            depth -= 1


def load_graph(path):
    """Read UTF-8 graph JSON without collapsing duplicate keys."""
    try:
        raw = path.read_text(encoding="utf-8")
        _check_json_depth(raw)
        return json.loads(raw, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise GraphError("Malformed graph JSON") from exc


def _require_lossless_json(graph):
    """Reject values whose hashed executable graph is not its JSON round-trip.

    Validation is also called with in-memory Python graphs, not just files
    decoded by load_graph(). Reject non-string mapping keys, tuples, NaN,
    unpaired surrogates and recursive objects before Simulation computes a
    digest or Aquarium opens a journal. Valid v1 JSON hashes are unchanged.
    """
    try:
        wire = json.dumps(graph, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
        wire.encode("utf-8", errors="strict")
        restored = json.loads(wire, object_pairs_hook=_unique_json_object)
    except GraphError:
        raise
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise GraphError("Graph v1 requires lossless UTF-8 JSON") from exc
    if restored != graph:
        raise GraphError("Graph v1 requires lossless UTF-8 JSON")


def validate(graph):
    if not isinstance(graph, dict) or graph.get("schema") != SCHEMA:
        raise GraphError("Unsupported or absent graph schema")
    # V1 graph identity and revision are part of the canonical wire format.
    # Missing or malformed metadata must fail before Simulation hashes this
    # object or Aquarium opens its durable SQLite journal.
    if set(graph) != {"schema", "id", "revision", "nodes", "edges"}:
        raise GraphError("Graph v1 requires exact root fields (unsupported v1 field)")
    for field in ("id", "revision"):
        value = graph[field]
        if type(value) is not str or not value.strip() or len(value) > 256:
            raise GraphError(f"Graph v1 {field} must be nonempty bounded text")
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise GraphError(f"Graph v1 {field} must be valid UTF-8") from exc
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not nodes or not isinstance(edges, list):
        raise GraphError("nodes must be nonempty list and edges must be list")
    if len(nodes) > 256 or len(edges) > 2048:
        raise GraphError("Graph exceeds safe bootstrap size")
    by_id = {}
    for n in nodes:
        if not isinstance(n, dict) or set(n) != {"id", "kind", "config", "notes"}:
            raise GraphError("Unsupported graph v1 node shape")
        if not isinstance(n["id"], str) or not n["id"]:
            raise GraphError("Every node requires a nonempty string ID")
        if n["id"] in by_id:
            raise GraphError("Duplicate node ID: " + n["id"])
        kind = n.get("kind")
        if not isinstance(kind, str) or kind not in PORTS:
            raise GraphError("Unsupported node kind: " + str(kind))
        if not isinstance(n.get("config"), dict) or not isinstance(n.get("notes"), dict):
            raise GraphError("Every node needs config and notes objects")
        purpose = n["notes"].get("purpose")
        if not isinstance(purpose, str) or not purpose.strip():
            raise GraphError("Node notes.purpose must be nonempty text")
        by_id[n["id"]] = n
    incoming = defaultdict(dict)
    adjacency = defaultdict(set)
    indegree = {name: 0 for name in by_id}
    for e in edges:
        # A mixed v1/alternate-v1 record may carry ignored source/target aliases
        # alongside canonical from/to, so reject any extra or absent wire keys.
        if not isinstance(e, dict) or set(e) != {"from", "to"}:
            raise GraphError("Unsupported graph v1 edge shape")
        src, dst = e["from"], e["to"]
        if (not isinstance(src, dict) or set(src) != {"node", "port"}
                or not isinstance(dst, dict) or set(dst) != {"node", "port"}):
            raise GraphError("Malformed edge endpoint")
        sid, sport, did, dport = src["node"], src["port"], dst["node"], dst["port"]
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
    _require_lossless_json(graph)
    return by_id, incoming, order
