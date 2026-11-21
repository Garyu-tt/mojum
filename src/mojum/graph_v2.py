"""Opt-in, NON-EXECUTABLE graph-v2 contract audit and lossless v1 migration.

V1 remains the sole executable Universal Graph IR. Visibility labels are
information-flow metadata, not permissions or access-control enforcement.
"""

import copy
import hashlib
import json

from .graph import GraphError, PORTS, SCHEMA as V1_SCHEMA, validate as validate_v1

V2_SCHEMA = "mechanicus.universal-graph/2"
_VISIBILITY_LEVEL = {"PUBLIC": 0, "PRIVATE": 1, "RESTRICTED": 2}


def _json_copy(value):
    """Require actual, losslessly serializable UTF-8 JSON, not Python coercions."""
    try:
        wire = json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
        wire.encode("utf-8", errors="strict")
        result = json.loads(wire)
        if result != value:
            raise GraphError("graph requires lossless JSON values")
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError) as exc:
        raise GraphError("graph requires canonical UTF-8 JSON") from exc
    return result


def _sha(value):
    wire = json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def upgrade_v1(graph, *, labels=None):
    """Copy a canonical v1 graph to a visibly distinct, audit-only v2 schema.

    No v1 file, journal head, graph hash or runtime is modified. Default
    visibility is PRIVATE; overrides are explicit, not inferred from kind.
    """
    original = _json_copy(graph)
    # Check reserved migration metadata before the stricter v1 validator.
    # Guard malformed inputs so they still fail as GraphError in validate_v1.
    if isinstance(original, dict) and (
        "origin" in original or (
            isinstance(original.get("nodes"), list) and any(
                isinstance(node, dict) and (
                    "visibility" in node or "ports" in node
                ) for node in original["nodes"]
            )
        )
    ):
        raise GraphError("v1 graph already contains reserved v2 metadata")
    validate_v1(original)
    if labels is None:
        labels = {}
    if not isinstance(labels, dict):
        raise GraphError("labels must map node IDs to visibility strings")
    node_ids = {node["id"] for node in original["nodes"]}
    if any(type(node_id) is not str or node_id not in node_ids for node_id in labels):
        raise GraphError("labels reference unknown node")
    if any(type(value) is not str or value not in _VISIBILITY_LEVEL for value in labels.values()):
        raise GraphError("unsupported node visibility")
    upgraded = copy.deepcopy(original)
    upgraded["schema"] = V2_SCHEMA
    upgraded["origin"] = {"schema": V1_SCHEMA, "sha256": _sha(original)}
    for node in upgraded["nodes"]:
        inputs, outputs = PORTS[node["kind"]]
        node["visibility"] = labels.get(node["id"], "PRIVATE")
        node["ports"] = {"inputs": dict(inputs), "outputs": dict(outputs)}
    validate_v2(upgraded)
    return upgraded


def validate_v2(graph):
    """Validate v2 labels and v1 provenance, returning a read-only audit.

    This is deliberately NOT a graph execution or declassification API.
    """
    checked = _json_copy(graph)
    if not isinstance(checked, dict) or checked.get("schema") != V2_SCHEMA:
        raise GraphError("unsupported graph-v2 schema")
    origin = checked.get("origin")
    if (not isinstance(origin, dict) or set(origin) != {"schema", "sha256"}
            or origin["schema"] != V1_SCHEMA or type(origin["sha256"]) is not str
            or len(origin["sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in origin["sha256"])):
        raise GraphError("invalid v1 graph provenance")
    nodes = checked.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise GraphError("graph-v2 requires nodes")
    legacy = copy.deepcopy(checked)
    legacy["schema"] = V1_SCHEMA
    del legacy["origin"]
    levels = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise GraphError("invalid graph-v2 node")
        node_id, kind = node.get("id"), node.get("kind")
        # Validate IDs and kinds in the original canonical v1 validator too.
        if type(node_id) is not str or type(kind) is not str or kind not in PORTS:
            raise GraphError("unknown graph-v2 node ID or kind")
        if type(node.get("visibility")) is not str or node["visibility"] not in _VISIBILITY_LEVEL:
            raise GraphError("unknown graph-v2 visibility")
        inputs, outputs = PORTS[kind]
        declared = node.get("ports")
        if (not isinstance(declared, dict) or set(declared) != {"inputs", "outputs"}
                or type(declared["inputs"]) is not dict or type(declared["outputs"]) is not dict
                or declared["inputs"] != inputs or declared["outputs"] != outputs):
            raise GraphError("graph-v2 ports do not match registered node kind")
        levels[node_id] = _VISIBILITY_LEVEL[node["visibility"]]
    for node in legacy["nodes"]:
        del node["visibility"]
        del node["ports"]
    validate_v1(legacy)  # Same canonical endpoints, type checking, DAG, and limits.
    if _sha(legacy) != origin["sha256"]:
        raise GraphError("v1 provenance checksum mismatch")
    for edge in legacy["edges"]:
        source = edge["from"]["node"]
        target = edge["to"]["node"]
        if levels[source] > levels[target]:
            raise GraphError("visibility downgrade along typed edge")
    return {
        "schema": V2_SCHEMA,
        "origin_sha256": origin["sha256"],
        "graph_sha256": _sha(checked),
        "node_count": len(nodes),
        "edge_count": len(legacy["edges"]),
        "execution": "NOT_SUPPORTED",
        "security": "labels-only-not-access-control",
    }
