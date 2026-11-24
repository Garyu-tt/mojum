"""Typed graph contract validator: no dynamically executable user code."""

from collections import defaultdict
import json
import math

SCHEMA = "mechanicus.universal-graph/1"
MAX_JSON_DEPTH = 256
# Match the offline graph audit input budget. This bounds decoding BEFORE JSON
# structure checks, so CLI/portable hosts cannot allocate arbitrarily large
# graph JSON just by passing a path to an untrusted file.
MAX_GRAPH_BYTES = 256 * 1024

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


def _require_lossless_json(graph):
    """Fail before hashing when nested v1 data cannot survive a UTF-8 JSON wire.

    Config and notes are intentionally extensible, but neither Python-only
    values nor unpaired surrogates are valid persisted graph data. Merely
    hashing with json.dumps could otherwise silently coerce tuples or map
    keys, or leak a UnicodeEncodeError after structural validation.
    """
    try:
        wire = json.dumps(graph, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
        wire.encode("utf-8", errors="strict")
        decoded = json.loads(wire)
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise GraphError("Graph requires lossless UTF-8 JSON values") from exc
    if decoded != graph:
        raise GraphError("Graph requires lossless UTF-8 JSON values")


def _reject_json_constant(token):
    raise GraphError(f"Noncanonical graph JSON numeric constant: {token}")


def _parse_json_float(token):
    number = float(token)
    if not math.isfinite(number):
        raise GraphError("Graph JSON number exceeds finite float range")
    return number


def load_graph(path):
    """Read bounded UTF-8 graph JSON without collapsing duplicate keys.

    Reading only MAX_GRAPH_BYTES + 1 bytes avoids creating an unbounded Python
    string before the depth, duplicate-key and numeric gates can inspect it.
    The byte limit also counts non-ASCII UTF-8 and insignificant whitespace.
    """
    try:
        with path.open("rb") as stream:
            payload = stream.read(MAX_GRAPH_BYTES + 1)
        if len(payload) > MAX_GRAPH_BYTES:
            raise GraphError("Graph JSON exceeds 256 KiB byte limit")
        raw = payload.decode("utf-8", errors="strict")
        _check_json_depth(raw)
        return json.loads(raw, object_pairs_hook=_unique_json_object,
                          parse_constant=_reject_json_constant,
                          parse_float=_parse_json_float)
    except GraphError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise GraphError("Malformed graph JSON") from exc


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
    # The exact v1 shape is checked above; reject non-JSON nested content
    # before callers record the graph revision or open a SQLite journal.
    _require_lossless_json(graph)
    return by_id, incoming, order
