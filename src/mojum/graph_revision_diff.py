"""Deterministic, bounded, read-only comparison of *validated* Universal Graph IR revisions.

Only v1 is executable.  V2 is a distinct, metadata-only audit schema; no
cross-version comparison, migration, journal access or declassification occurs.
The report contains counts and opaque SHA-256 digests, never node IDs, notes,
configs, graph IDs or individual visibility labels.
"""

from __future__ import annotations

import hashlib
import json
import math

from .graph import GraphError, SCHEMA as V1_SCHEMA, validate as validate_v1
from .graph_v2 import V2_SCHEMA, validate_v2

REPORT_SCHEMA = "mojum.graph-revision-diff/1"
MAX_GRAPH_BYTES = 256 * 1024
MAX_JSON_DEPTH = 128
MAX_JSON_VALUES = 100_000


def _bounded_wire(value):
    """Require ordinary JSON types and cap serialized size before graph validation.

    Walk iteratively so a self-referential or deeply nested hostile Python
    object cannot cause unbounded validator recursion.  Exact types reject
    user-defined equality/serialization shims.
    """
    pending = [(value, 0)]
    visited = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        if visited > MAX_JSON_VALUES or depth > MAX_JSON_DEPTH:
            raise GraphError("graph exceeds comparison JSON complexity budget")
        typ = type(item)
        if typ is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise GraphError("graph requires string JSON object keys")
                pending.append((key, depth + 1))
                pending.append((child, depth + 1))
        elif typ is list:
            pending.extend((child, depth + 1) for child in item)
        elif typ is str:
            try:
                item.encode("utf-8", "strict")
            except UnicodeError as exc:
                raise GraphError("graph requires valid UTF-8") from exc
        elif typ is float:
            if not math.isfinite(item):
                raise GraphError("graph requires finite JSON numbers")
        elif typ not in (int, bool, type(None)):
            raise GraphError("graph requires exact JSON value types")
    try:
        chunks = []
        total = 0
        encoder = json.JSONEncoder(
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        for chunk in encoder.iterencode(value):
            part = chunk.encode("utf-8", "strict")
            total += len(part)
            if total > MAX_GRAPH_BYTES:
                raise GraphError("graph exceeds comparison 256 KiB byte budget")
            chunks.append(part)
        return b"".join(chunks)
    except GraphError:
        raise
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise GraphError("graph is not canonical bounded UTF-8 JSON") from exc


def _digest(value):
    raw = json.dumps(
        value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8", "strict")
    return hashlib.sha256(raw).hexdigest()


def _read_checked(value):
    wire = _bounded_wire(value)
    if type(value) is not dict:
        raise GraphError("graph must be a JSON object")
    schema = value.get("schema")
    if schema == V1_SCHEMA:
        validate_v1(value)
    elif schema == V2_SCHEMA:
        validate_v2(value)
    else:
        raise GraphError("unsupported graph revision schema")
    return schema, wire


def _edge_set(graph):
    return {
        (
            edge["from"]["node"], edge["from"]["port"],
            edge["to"]["node"], edge["to"]["port"],
        )
        for edge in graph["edges"]
    }


def _semantic_graph(graph, schema):
    """Normalize node/edge list ordering, excluding *revision*, not content."""
    nodes = []
    for node in sorted(graph["nodes"], key=lambda n: n["id"]):
        record = {
            "id": node["id"], "kind": node["kind"],
            "config": node["config"], "notes": node["notes"],
        }
        if schema == V2_SCHEMA:
            record["visibility"] = node["visibility"]
        nodes.append(record)
    return {
        "schema": schema, "id": graph["id"],
        "nodes": nodes, "edges": sorted(_edge_set(graph)),
    }


def compare_revisions(before, after):
    """Return a redacted, deterministic comparison of two same-schema graphs.

    Both input graphs must be independently valid and share graph identity.
    Separate version families cannot be compared by pretending v2 runs on the
    v1 executor.  SHA-256 gives integrity fingerprints, not authentication.
    Neither input is modified.  No config/notes/identifier is emitted.
    """
    source_schema, before_wire = _read_checked(before)
    target_schema, after_wire = _read_checked(after)
    if source_schema != target_schema:
        raise GraphError("mixed graph schemas require explicit migration before diff")
    if before["id"] != after["id"]:
        raise GraphError("cannot diff different graph identities")
    original = {node["id"]: node for node in before["nodes"]}
    revised = {node["id"]: node for node in after["nodes"]}
    shared = original.keys() & revised.keys()
    prior_edges = _edge_set(before)
    next_edges = _edge_set(after)
    counts = {
        "nodes_added": len(revised.keys() - original.keys()),
        "nodes_removed": len(original.keys() - revised.keys()),
        "node_kind_changed": sum(
            original[node]["kind"] != revised[node]["kind"] for node in shared
        ),
        "node_config_changed": sum(
            _digest(original[node]["config"]) != _digest(revised[node]["config"])
            for node in shared
        ),
        "node_notes_changed": sum(
            _digest(original[node]["notes"]) != _digest(revised[node]["notes"])
            for node in shared
        ),
        "node_visibility_changed": sum(
            original[node]["visibility"] != revised[node]["visibility"]
            for node in shared
        ) if source_schema == V2_SCHEMA else 0,
        "edges_added": len(next_edges - prior_edges),
        "edges_removed": len(prior_edges - next_edges),
    }
    before_semantic = _digest(_semantic_graph(before, source_schema))
    after_semantic = _digest(_semantic_graph(after, source_schema))
    return {
        "schema": REPORT_SCHEMA,
        "input_schema": source_schema,
        "before_sha256": hashlib.sha256(before_wire).hexdigest(),
        "after_sha256": hashlib.sha256(after_wire).hexdigest(),
        "before_semantic_sha256": before_semantic,
        "after_semantic_sha256": after_semantic,
        "wire_changed": before_wire != after_wire,
        "revision_changed": before["revision"] != after["revision"],
        "semantic_changed": before_semantic != after_semantic,
        "change_counts": counts,
        "execution": "NOT_PERFORMED",
        "security": "METADATA_ONLY_NOT_ACCESS_CONTROL_OR_SIGNATURE",
        "redaction": "NO_IDENTIFIERS_CONFIGS_OR_NOTES",
    }
