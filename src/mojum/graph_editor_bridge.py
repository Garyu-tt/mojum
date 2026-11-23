"""Optional, no-dependency React Flow / Rete.js graph editor wire bridge.

This is an offline adapter, NOT an installed editor, a v2 executor, a
hot-swap API, or an access-control boundary. No graph, journal, or process is
changed by this module. Import returns a candidate only after CAS checks.
"""
from __future__ import annotations

import copy
import json
import math

from .graph import GraphError, _check_json_depth
from .graph_v2 import V2_SCHEMA, downgrade_v2, upgrade_v1, validate_v2

WIRE_SCHEMA = "mojum.graph-editor-envelope/1"
BACKENDS = frozenset(("reactflow", "rete"))
MAX_WIRE_BYTES = 512 * 1024
MAX_POSITION = 100_000


def _copy_json(value):
    """Reject lossy Python objects, unsafe nesting and oversized payloads."""
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         allow_nan=False, separators=(",", ":"))
        if len(raw.encode("utf-8", errors="strict")) > MAX_WIRE_BYTES:
            raise GraphError("editor payload exceeds byte limit")
        _check_json_depth(raw)
        result = json.loads(raw)
        if result != value:
            raise GraphError("editor payload must be lossless JSON")
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        if isinstance(exc, GraphError):
            raise
        raise GraphError("invalid editor JSON payload") from exc


def _graph_sha(graph):
    import hashlib
    raw = json.dumps(graph, ensure_ascii=False, sort_keys=True,
                     allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _check_private(graph, allow_private):
    if type(allow_private) is not bool:
        raise GraphError("allow_private must be a boolean")
    if not allow_private and any(n["visibility"] != "PUBLIC" for n in graph["nodes"]):
        raise GraphError("non-PUBLIC graph requires explicit allow_private=True")


def _node_view(node, backend, index):
    position = {"x": (index % 4) * 240, "y": (index // 4) * 160}
    data = {key: copy.deepcopy(node[key]) for key in
            ("kind", "config", "notes", "visibility", "ports")}
    if backend == "reactflow":
        return {"id": node["id"], "type": "mojum-node",
                "position": position, "data": data}
    return {
        "id": node["id"], "label": node["kind"],
        "position": position,
        "inputs": {name: {"socket": typ}
                   for name, typ in node["ports"]["inputs"].items()},
        "outputs": {name: {"socket": typ}
                    for name, typ in node["ports"]["outputs"].items()},
        "data": data,
    }


def _edge_view(edge, backend, index):
    src, dst = edge["from"], edge["to"]
    if backend == "reactflow":
        return {"id": f"e{index}", "source": src["node"],
                "sourceHandle": src["port"], "target": dst["node"],
                "targetHandle": dst["port"]}
    return {"id": f"e{index}", "source": src["node"],
            "sourceOutput": src["port"], "target": dst["node"],
            "targetInput": dst["port"]}


def export_editor(graph, backend, *, allow_private=False):
    """Project a validated v2 graph to framework-shaped offline JSON.

    PRIVATE/RESTRICTED graph contents are *not* exported without an explicit
    local opt-in. No packages are imported and no network requests are made.
    """
    if type(backend) is not str or backend not in BACKENDS:
        raise GraphError("unknown editor backend")
    checked = _copy_json(graph)
    if not isinstance(checked, dict) or checked.get("schema") != V2_SCHEMA:
        raise GraphError("editor bridge requires canonical graph v2")
    validate_v2(checked)
    _check_private(checked, allow_private)
    result = {
        "schema": WIRE_SCHEMA,
        "backend": backend,
        "revision": checked["revision"],
        "source_sha256": _graph_sha(checked),
        "canonical": checked,
        "nodes": [_node_view(n, backend, i) for i, n in
                  enumerate(checked["nodes"])],
        "edges": [_edge_view(e, backend, i) for i, e in
                  enumerate(checked["edges"])],
    }
    return _copy_json(result)


def _valid_position(position):
    if not isinstance(position, dict) or set(position) != {"x", "y"}:
        raise GraphError("invalid editor position")
    for key in ("x", "y"):
        n = position[key]
        if type(n) not in (int, float) or not math.isfinite(n) or abs(n) > MAX_POSITION:
            raise GraphError("editor position is not finite and bounded")


def import_editor(envelope, *, expected_revision, expected_sha256,
                  new_revision=None, allow_private=False):
    """Validate editor input and return a detached, non-executable candidate.

    Only positions and notes may change. A notes change requires a distinct
    explicitly supplied new_revision; origin SHA is then rebuilt by existing
    canonical v1->v2 migration, never patched in place. All other mutations
    fail closed, including unknown/nested graph shapes. CAS compares both the
    original revision and canonical SHA to the caller's independent anchor.
    """
    wire = _copy_json(envelope)
    if not isinstance(wire, dict) or set(wire) != {
        "schema", "backend", "revision", "source_sha256",
        "canonical", "nodes", "edges",
    } or wire["schema"] != WIRE_SCHEMA:
        raise GraphError("unsupported editor envelope")
    backend = wire["backend"]
    if type(backend) is not str or backend not in BACKENDS:
        raise GraphError("unknown editor backend")
    original = wire["canonical"]
    if not isinstance(original, dict):
        raise GraphError("editor envelope needs canonical graph")
    validate_v2(original)
    _check_private(original, allow_private)
    if (type(expected_revision) is not str or
            type(expected_sha256) is not str or
            len(expected_sha256) != 64 or
            any(c not in "0123456789abcdef" for c in expected_sha256)):
        raise GraphError("independent revision and SHA anchors are required")
    sha = _graph_sha(original)
    if (original["revision"] != expected_revision or
            wire["revision"] != expected_revision or
            wire["source_sha256"] != expected_sha256 or
            sha != expected_sha256):
        raise GraphError("stale or tampered editor base (CAS mismatch)")
    reference = export_editor(original, backend, allow_private=allow_private)
    if type(wire["edges"]) is not list or wire["edges"] != reference["edges"]:
        raise GraphError("editor edges are not a supported edit")
    if type(wire["nodes"]) is not list or len(wire["nodes"]) != len(reference["nodes"]):
        raise GraphError("editor nodes are missing or unexpected")
    updated_notes = {}
    layout = []
    modified = False
    for node, baseline in zip(wire["nodes"], reference["nodes"]):
        if type(node) is not dict or set(node) != set(baseline):
            raise GraphError("unexpected editor node shape")
        if node.get("id") != baseline["id"]:
            raise GraphError("editor node ordering/identity changed")
        for key in baseline:
            if key not in ("position", "data") and node[key] != baseline[key]:
                raise GraphError("unsupported editor node mutation")
        _valid_position(node["position"])
        data = node["data"]
        base_data = baseline["data"]
        if type(data) is not dict or set(data) != set(base_data):
            raise GraphError("editor data fields changed")
        for key in base_data:
            if key != "notes" and data[key] != base_data[key]:
                raise GraphError("editor changed immutable graph metadata")
        notes = data["notes"]
        if type(notes) is not dict:
            raise GraphError("editor notes must be an object")
        modified |= notes != base_data["notes"]
        updated_notes[node["id"]] = copy.deepcopy(notes)
        layout.append({"id": node["id"],
                       "position": copy.deepcopy(node["position"])})
    if modified:
        if (type(new_revision) is not str or not new_revision.strip()
                or len(new_revision) > 256 or new_revision == expected_revision):
            raise GraphError("edited notes require a distinct new revision")
        legacy = downgrade_v2(original)
        legacy["revision"] = new_revision
        for node in legacy["nodes"]:
            node["notes"] = updated_notes[node["id"]]
        labels = {node["id"]: node["visibility"] for node in original["nodes"]}
        candidate = upgrade_v1(legacy, labels=labels)
    else:
        if new_revision is not None:
            raise GraphError("revision bump requires an actual notes edit")
        candidate = copy.deepcopy(original)
    validate_v2(candidate)
    return {
        "graph": candidate,
        "layout": layout,
        "notes_changed": modified,
        "execution": "NOT_PERFORMED",
        "hot_swap": "NOT_PERFORMED",
    }
