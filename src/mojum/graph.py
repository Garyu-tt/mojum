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


# Direct-Python callers must obey the same finite wire budget as load_graph().
MAX_GRAPH_TREE_NODES = 50_000


class GraphError(ValueError):
    pass


def _require_native_json_tree(value):
    """Admit only a bounded, detached-shape JSON tree made from exact built-ins.

    This runs before any schema set comparison or topology lookup. It avoids
    invoking caller-defined equality/hash/iteration hooks and rejects Python
    object graphs (cycles and shared containers) that have no JSON tree form.
    """
    if type(value) is not dict:
        raise GraphError("Graph must be an exact built-in JSON object")
    pending = [(value, 1)]
    seen_containers = set()
    node_count = 0
    text_bytes = 0

    try:
        while pending:
            current, depth = pending.pop()
            node_count += 1
            if node_count > MAX_GRAPH_TREE_NODES:
                raise GraphError("Graph JSON tree exceeds node budget")
            if depth > MAX_JSON_DEPTH:
                raise GraphError("Graph JSON nesting depth exceeded")

            kind = type(current)
            if kind is dict:
                identity = id(current)
                if identity in seen_containers:
                    raise GraphError("Graph JSON tree contains a cycle or shared container")
                seen_containers.add(identity)
                if len(current) > MAX_GRAPH_TREE_NODES:
                    raise GraphError("Graph JSON object exceeds node budget")
                for key, child in current.items():
                    node_count += 1
                    if node_count > MAX_GRAPH_TREE_NODES:
                        raise GraphError("Graph JSON tree exceeds node budget")
                    if type(key) is not str:
                        raise GraphError("Graph JSON object keys must be exact strings")
                    try:
                        text_bytes += len(key.encode("utf-8", errors="strict"))
                    except UnicodeEncodeError as exc:
                        raise GraphError("Graph JSON text must be valid UTF-8") from exc
                    if text_bytes > MAX_GRAPH_BYTES:
                        raise GraphError("Graph JSON exceeds 256 KiB byte limit")
                    pending.append((child, depth + 1))
            elif kind is list:
                identity = id(current)
                if identity in seen_containers:
                    raise GraphError("Graph JSON tree contains a cycle or shared container")
                seen_containers.add(identity)
                if len(current) > MAX_GRAPH_TREE_NODES:
                    raise GraphError("Graph JSON array exceeds node budget")
                pending.extend((child, depth + 1) for child in current)
            elif kind is str:
                try:
                    text_bytes += len(current.encode("utf-8", errors="strict"))
                except UnicodeEncodeError as exc:
                    raise GraphError("Graph JSON text must be valid UTF-8") from exc
                if text_bytes > MAX_GRAPH_BYTES:
                    raise GraphError("Graph JSON exceeds 256 KiB byte limit")
            elif kind is int:
                # Keep decimal conversion/serialization bounded even for a
                # direct caller that bypasses load_graph()'s byte limit.
                if current.bit_length() > MAX_GRAPH_BYTES * 3:
                    raise GraphError("Graph JSON integer exceeds byte budget")
            elif kind is float:
                if not math.isfinite(current):
                    raise GraphError("Graph JSON numbers must be finite")
            elif kind is bool or current is None:
                pass
            else:
                raise GraphError("Graph contains a non-native JSON value")
    except GraphError:
        raise
    except (RuntimeError, RecursionError, OverflowError, UnicodeError, ValueError) as exc:
        raise GraphError("Graph contains an invalid or changing JSON tree") from exc


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
        wire_bytes = wire.encode("utf-8", errors="strict")
        if len(wire_bytes) > MAX_GRAPH_BYTES:
            raise GraphError("Graph JSON exceeds 256 KiB byte limit")
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
    # Validate the complete caller-owned object tree before any dict/set
    # comparison, membership operation, or topology lookup can invoke custom
    # Python behavior. The same limits apply to load_graph() and direct users.
    _require_native_json_tree(graph)
    if type(graph) is not dict or graph.get("schema") != SCHEMA:
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
    if type(nodes) is not list or not nodes or type(edges) is not list:
        raise GraphError("nodes must be nonempty list and edges must be list")
    if len(nodes) > 256 or len(edges) > 2048:
        raise GraphError("Graph exceeds safe bootstrap size")
    by_id = {}
    for n in nodes:
        if type(n) is not dict or set(n) != {"id", "kind", "config", "notes"}:
            raise GraphError("Unsupported graph v1 node shape")
        if type(n["id"]) is not str or not n["id"]:
            raise GraphError("Every node requires a nonempty string ID")
        if n["id"] in by_id:
            raise GraphError("Duplicate node ID: " + n["id"])
        kind = n.get("kind")
        if type(kind) is not str or kind not in PORTS:
            raise GraphError("Unsupported node kind: " + str(kind))
        if type(n.get("config")) is not dict or type(n.get("notes")) is not dict:
            raise GraphError("Every node needs config and notes objects")
        purpose = n["notes"].get("purpose")
        if type(purpose) is not str or not purpose.strip():
            raise GraphError("Node notes.purpose must be nonempty text")
        by_id[n["id"]] = n
    incoming = defaultdict(dict)
    adjacency = defaultdict(set)
    indegree = {name: 0 for name in by_id}
    for e in edges:
        # A mixed v1/alternate-v1 record may carry ignored source/target aliases
        # alongside canonical from/to, so reject any extra or absent wire keys.
        if type(e) is not dict or set(e) != {"from", "to"}:
            raise GraphError("Unsupported graph v1 edge shape")
        src, dst = e["from"], e["to"]
        if (type(src) is not dict or set(src) != {"node", "port"}
                or type(dst) is not dict or set(dst) != {"node", "port"}):
            raise GraphError("Malformed edge endpoint")
        sid, sport, did, dport = src["node"], src["port"], dst["node"], dst["port"]
        if not all(type(value) is str for value in (sid, sport, did, dport)):
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
