"""Offline, lossless UI handoff for canonical Universal Graph IR v1.

UI libraries are presentation layers, never graph authorities or executors.
"""
import copy
import hashlib
import json

from .graph import GraphError, validate

SCHEMA = "mojum.graph-ui-handoff/1"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def export_handoff(graph):
    """Produce a detached, immutable-by-convention editor input envelope."""
    validate(graph)
    wire = _canonical(graph)
    return {"schema": SCHEMA, "graph_id": graph["id"],
            "base_revision": graph["revision"],
            "base_sha256": hashlib.sha256(wire).hexdigest(),
            "graph": copy.deepcopy(graph)}


def import_handoff(handoff, current_graph):
    """Admit a proposed editor graph only against its exact unchanged base.

    No live state is modified. The caller must persist through its own
    authoritative compare-and-swap after this preflight.
    """
    validate(current_graph)
    if not isinstance(handoff, dict) or set(handoff) != {
        "schema", "graph_id", "base_revision", "base_sha256", "graph"
    } or handoff["schema"] != SCHEMA:
        raise GraphError("Unsupported graph UI handoff")
    if (handoff["graph_id"] != current_graph["id"]
            or handoff["base_revision"] != current_graph["revision"]
            or handoff["base_sha256"] != hashlib.sha256(
                _canonical(current_graph)).hexdigest()):
        raise GraphError("Stale graph UI handoff: base identity/revision/hash mismatch")
    candidate = handoff["graph"]
    validate(candidate)
    if candidate["id"] != current_graph["id"]:
        raise GraphError("Editor cannot change canonical graph identity")
    if candidate["revision"] == current_graph["revision"] and candidate != current_graph:
        raise GraphError("Edited graph requires a new revision")
    return copy.deepcopy(candidate)
