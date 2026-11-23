"""Offline nested-container projection for the optional graph editor bridge.

The canonical Universal Graph remains unchanged and executable only through
its existing v1 runtime. This module defines a detached editor-only envelope
that groups a separately validated graph-v2 child behind explicit typed
portals. It never executes, persists, fetches, or mutates a graph.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math

from .graph import GraphError, PORTS
from .graph_v2 import validate_v2

NESTED_SCHEMA = "mojum.graph-editor-nested/1"
BACKENDS = frozenset(("reactflow", "rete"))
MAX_BYTES = 1024 * 1024
MAX_CONTAINERS = 64
MAX_PORTALS = 32
MAX_POSITION = 100_000


MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 50_000


def _preflight_json(value):
    """Bound and validate an exact-native JSON tree before serialization.

    The traversal never invokes caller-defined container/string methods. Shared
    containers are rejected as well as cycles so the admitted tree has one
    unambiguous ownership path and bounded traversal cost.
    """
    stack = [(value, 0)]
    seen = set()
    nodes = 0
    estimated_bytes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise GraphError("nested editor JSON structure exceeds limits")
        kind = type(item)
        if kind is dict:
            identity = id(item)
            if identity in seen:
                raise GraphError("nested editor JSON contains a cycle or shared container")
            seen.add(identity)
            # Charge structural overhead before adding children to the worklist.
            estimated_bytes += 2 + len(item) * 6
            if estimated_bytes > MAX_BYTES:
                raise GraphError("nested editor payload exceeds byte limit")
            for key, child in item.items():
                if type(key) is not str:
                    raise GraphError("nested editor JSON keys must be native strings")
                if len(key) > MAX_BYTES:
                    raise GraphError("nested editor JSON key exceeds byte limit")
                try:
                    key_bytes = len(key.encode("utf-8", errors="strict"))
                except UnicodeEncodeError as exc:
                    raise GraphError("nested editor JSON key is not valid UTF-8") from exc
                if key_bytes > MAX_BYTES:
                    raise GraphError("nested editor JSON key exceeds byte limit")
                estimated_bytes += key_bytes
                stack.append((child, depth + 1))
        elif kind is list:
            identity = id(item)
            if identity in seen:
                raise GraphError("nested editor JSON contains a cycle or shared container")
            seen.add(identity)
            estimated_bytes += 2 + len(item)
            if estimated_bytes > MAX_BYTES:
                raise GraphError("nested editor payload exceeds byte limit")
            stack.extend((child, depth + 1) for child in item)
        elif kind is str:
            if len(item) > MAX_BYTES:
                raise GraphError("nested editor JSON text exceeds byte limit")
            try:
                estimated_bytes += len(item.encode("utf-8", errors="strict")) + 2
            except UnicodeEncodeError as exc:
                raise GraphError("nested editor JSON text is not valid UTF-8") from exc
        elif kind is int:
            if item.bit_length() > 64:
                raise GraphError("nested editor JSON integer exceeds limit")
            estimated_bytes += 24
        elif kind is float:
            if not math.isfinite(item):
                raise GraphError("nested editor JSON number must be finite")
            estimated_bytes += 24
        elif item is None or kind is bool:
            estimated_bytes += 5
        else:
            raise GraphError("nested editor payload must use exact native JSON values")
        if estimated_bytes > MAX_BYTES:
            raise GraphError("nested editor payload exceeds byte limit")


def _copy_json(value):
    _preflight_json(value)
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         allow_nan=False, separators=(",", ":"))
        if len(raw.encode("utf-8", errors="strict")) > MAX_BYTES:
            raise GraphError("nested editor payload exceeds byte limit")
        result = json.loads(raw)
        return result
    except GraphError:
        raise
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise GraphError("invalid nested editor JSON payload") from exc


def _sha(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _graph_nodes(graph):
    validate_v2(graph)
    return {node["id"]: node for node in graph["nodes"]}


def _source_sha(parent, containers):
    # Layout is intentionally excluded: it is the only editable field.
    stable = [{
        "id": item["id"], "parent_node": item["parent_node"],
        "child_graph": item["child_graph"], "portals": item["portals"],
    } for item in containers]
    return _sha({"parent_graph": parent, "containers": stable})


def _ports(node, direction):
    try:
        declared = PORTS[node["kind"]]
        return declared[0 if direction == "input" else 1]
    except (KeyError, TypeError, IndexError) as exc:
        raise GraphError("unknown nested graph node kind") from exc


def _position(value):
    if type(value) is not dict or set(value) != {"x", "y"}:
        raise GraphError("nested container position requires x/y")
    for axis in ("x", "y"):
        number = value[axis]
        try:
            finite = math.isfinite(number) if type(number) in (int, float) else False
        except OverflowError:
            finite = False
        if not finite or abs(number) > MAX_POSITION:
            raise GraphError("nested container position is not finite and bounded")
    return {"x": value["x"], "y": value["y"]}


def validate_nested(parent_graph, containers):
    """Return a detached normalized editor-only nested-container model.

    A container is anchored to one registered node in the parent graph and
    owns a separately valid graph-v2 child. Portals bind same-direction,
    same-typed registered ports across that boundary. This is a visual/editing
    contract only; it does not rewrite parent/child executable graphs.
    """
    parent = _copy_json(parent_graph)
    parent_nodes = _graph_nodes(parent)
    items = _copy_json(containers)
    if type(items) is not list or len(items) > MAX_CONTAINERS:
        raise GraphError("nested container count exceeds limit")
    seen_ids = set()
    seen_parent_nodes = set()
    normalized = []
    for item in items:
        if type(item) is not dict or set(item) != {
            "id", "parent_node", "child_graph", "portals", "position"
        }:
            raise GraphError("invalid nested container record")
        cid, parent_id = item["id"], item["parent_node"]
        if (type(cid) is not str or not cid or len(cid) > 128
                or cid in seen_ids):
            raise GraphError("invalid or duplicate nested container ID")
        seen_ids.add(cid)
        if (type(parent_id) is not str or parent_id not in parent_nodes
                or parent_id in seen_parent_nodes):
            raise GraphError("nested container parent node is absent or already claimed")
        seen_parent_nodes.add(parent_id)
        child = _copy_json(item["child_graph"])
        child_nodes = _graph_nodes(child)
        portals = _copy_json(item["portals"])
        if type(portals) is not list or len(portals) > MAX_PORTALS:
            raise GraphError("nested portal count exceeds limit")
        portal_ids, outer_ports, inner_ports = set(), set(), set()
        clean_portals = []
        for portal in portals:
            if type(portal) is not dict or set(portal) != {
                "id", "direction", "node", "port", "child_node", "child_port", "type"
            }:
                raise GraphError("invalid nested portal record")
            pid, direction = portal["id"], portal["direction"]
            if (type(pid) is not str or not pid or len(pid) > 128
                    or pid in portal_ids or type(direction) is not str
                    or direction not in ("input", "output")):
                raise GraphError("invalid or duplicate nested portal ID/direction")
            portal_ids.add(pid)
            outer_id, inner_id = portal["node"], portal["child_node"]
            outer_port, inner_port = portal["port"], portal["child_port"]
            if (type(outer_id) is not str or outer_id != parent_id
                    or type(inner_id) is not str or inner_id not in child_nodes
                    or type(outer_port) is not str or type(inner_port) is not str):
                raise GraphError("nested portal endpoint is absent or not anchored")
            outer_type = _ports(parent_nodes[outer_id], direction).get(outer_port)
            inner_type = _ports(child_nodes[inner_id], direction).get(inner_port)
            if (type(portal["type"]) is not str or outer_type is None
                    or inner_type is None or outer_type != inner_type
                    or portal["type"] != outer_type):
                raise GraphError("nested portal type/direction mismatch")
            outer_key = (direction, outer_id, outer_port)
            inner_key = (direction, inner_id, inner_port)
            if outer_key in outer_ports or inner_key in inner_ports:
                raise GraphError("nested portal endpoint is mapped more than once")
            outer_ports.add(outer_key)
            inner_ports.add(inner_key)
            clean_portals.append({
                "id": pid, "direction": direction, "node": outer_id,
                "port": outer_port, "child_node": inner_id,
                "child_port": inner_port, "type": outer_type,
            })
        clean_portals.sort(key=lambda p: p["id"])
        normalized.append({
            "id": cid, "parent_node": parent_id, "child_graph": child,
            "portals": clean_portals, "position": _position(item["position"]),
        })
    normalized.sort(key=lambda c: c["id"])
    return {"schema": NESTED_SCHEMA, "parent_graph": parent,
            "containers": normalized}


def export_nested_editor(parent_graph, containers, backend):
    """Project validated containers to a stable React Flow/Rete-shaped wire."""
    if type(backend) is not str or backend not in BACKENDS:
        raise GraphError("unknown nested editor backend")
    model = validate_nested(parent_graph, containers)
    canonical = model["parent_graph"]
    nodes = []
    for item in model["containers"]:
        data = {
            "parent_node": item["parent_node"],
            "child_graph": copy.deepcopy(item["child_graph"]),
            "portals": copy.deepcopy(item["portals"]),
        }
        if backend == "reactflow":
            nodes.append({"id": item["id"], "type": "mojum-container",
                          "position": copy.deepcopy(item["position"]),
                          "data": data})
        else:
            nodes.append({"id": item["id"], "label": "mojum-container",
                          "position": copy.deepcopy(item["position"]),
                          "inputs": {}, "outputs": {}, "data": data})
    return _copy_json({
        "schema": NESTED_SCHEMA, "backend": backend,
        "parent_revision": canonical["revision"],
        "source_sha256": _source_sha(canonical, model["containers"]),
        "parent_graph": canonical, "nodes": nodes,
    })


def import_nested_editor(envelope, *, expected_revision, expected_sha256):
    """Return detached container/layout candidates after parent-graph CAS.

    Child graphs and portal definitions are immutable in this first version;
    only editor positions may change. Structural edits require a future schema
    rather than being silently discarded or partially applied.
    """
    wire = _copy_json(envelope)
    if type(wire) is not dict or set(wire) != {
        "schema", "backend", "parent_revision", "source_sha256",
        "parent_graph", "nodes"
    } or wire["schema"] != NESTED_SCHEMA:
        raise GraphError("unsupported nested editor envelope")
    backend = wire["backend"]
    if type(backend) is not str or backend not in BACKENDS:
        raise GraphError("unknown nested editor backend")
    parent = _copy_json(wire["parent_graph"])
    _graph_nodes(parent)
    if (type(expected_revision) is not str or type(expected_sha256) is not str
            or len(expected_sha256) != 64
            or any(c not in "0123456789abcdef" for c in expected_sha256)
            or parent["revision"] != expected_revision
            or wire["parent_revision"] != expected_revision
            or type(wire["source_sha256"]) is not str
            or len(wire["source_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in wire["source_sha256"])):
        raise GraphError("nested editor parent CAS mismatch")
    if type(wire["nodes"]) is not list or len(wire["nodes"]) > MAX_CONTAINERS:
        raise GraphError("nested editor container inventory exceeds limit")
    normalized, layouts = [], []
    for node in wire["nodes"]:
        if type(node) is not dict:
            raise GraphError("invalid nested editor node")
        if backend == "reactflow":
            required = {"id", "type", "position", "data"}
            if set(node) != required or node["type"] != "mojum-container":
                raise GraphError("unexpected React Flow container shape")
        else:
            required = {"id", "label", "position", "inputs", "outputs", "data"}
            if (set(node) != required or node["label"] != "mojum-container"
                    or node["inputs"] != {} or node["outputs"] != {}):
                raise GraphError("unexpected Rete container shape")
        data = node["data"]
        if type(data) is not dict or set(data) != {
            "parent_node", "child_graph", "portals"
        }:
            raise GraphError("nested editor data shape changed")
        position = _position(node["position"])
        layouts.append({"id": node["id"], "position": position})
        normalized.append({
            "id": node["id"], "parent_node": data["parent_node"],
            "child_graph": data["child_graph"], "portals": data["portals"],
            "position": position,
        })
    model = validate_nested(parent, normalized)
    if (_source_sha(parent, model["containers"]) != expected_sha256
            or wire["source_sha256"] != expected_sha256):
        raise GraphError("nested editor source CAS mismatch")
    return {"schema": NESTED_SCHEMA, "parent_graph": copy.deepcopy(parent),
            "containers": model["containers"], "layout": layouts,
            "execution": "NOT_PERFORMED", "hot_swap": "NOT_PERFORMED"}
