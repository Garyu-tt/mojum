"""Advisory information-flow audit for canonical Universal Graph v1.

Visibility labels are caller-supplied metadata, not authenticated permissions.
This module never executes graphs or changes canonical graph/runtime state.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any

from . import graph as canonical_graph

LABEL_SCHEMA = "mojum.graph.v1.flow-labels/1"
REPORT_SCHEMA = "mojum.graph.v1.flow-audit-report/1"
MAX_JSON_BYTES = 256 * 1024
MAX_JSON_DEPTH = 256
MAX_JSON_ITEMS = 50_000
_LABEL_LEVEL = {"public": 0, "restricted": 1, "private": 2}


class GraphV1FlowAuditError(ValueError):
    """Invalid graph or visibility sidecar; no audit result was produced."""


@dataclass(frozen=True)
class FlowViolation:
    """Redacted edge-level policy finding; graph identifiers are omitted."""

    edge_index: int
    source_visibility: str
    target_visibility: str
    code: str


@dataclass(frozen=True)
class GraphV1FlowAudit:
    schema: str
    graph_sha256: str
    labels_sha256: str
    node_count: int
    edge_count: int
    allowed: bool
    violation_count: int
    violations: tuple[FlowViolation, ...]
    advisory_only: bool = True
    enforces_runtime_permissions: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["violations"] = [asdict(item) for item in self.violations]
        return result


def _check_json_tree(value: Any) -> None:
    """Admit exact JSON values without expanding beyond either resource budget."""
    pending = [(value, 0)]
    seen = 0
    text_bytes = 0

    def charge_text(text: str) -> None:
        nonlocal text_bytes
        # Check character length before encoding so one hostile huge string
        # cannot allocate an equally huge temporary UTF-8 buffer.
        if len(text) > MAX_JSON_BYTES:
            raise GraphV1FlowAuditError("Canonical JSON exceeds 256 KiB")
        try:
            size = len(text.encode("utf-8", errors="strict"))
        except UnicodeEncodeError as exc:
            raise GraphV1FlowAuditError("JSON text is not valid UTF-8") from exc
        text_bytes += size
        if text_bytes > MAX_JSON_BYTES:
            raise GraphV1FlowAuditError("Canonical JSON exceeds 256 KiB")

    while pending:
        current, depth = pending.pop()
        seen += 1
        if seen > MAX_JSON_ITEMS:
            raise GraphV1FlowAuditError("JSON item budget exceeded")
        if depth > MAX_JSON_DEPTH:
            raise GraphV1FlowAuditError("JSON nesting depth exceeded")
        kind = type(current)
        if kind is dict:
            # Count already-scheduled plus new children before creating any
            # traversal entries. This keeps peak auxiliary memory O(limit),
            # rather than O(the caller's untrusted container cardinality).
            if seen + len(pending) + len(current) > MAX_JSON_ITEMS:
                raise GraphV1FlowAuditError("JSON item budget exceeded")
            for key, child in current.items():
                if type(key) is not str:
                    raise GraphV1FlowAuditError("JSON object keys must be exact strings")
                charge_text(key)
                pending.append((child, depth + 1))
        elif kind is list:
            if seen + len(pending) + len(current) > MAX_JSON_ITEMS:
                raise GraphV1FlowAuditError("JSON item budget exceeded")
            pending.extend((child, depth + 1) for child in current)
        elif kind is str:
            charge_text(current)
        elif kind is float:
            if not math.isfinite(current):
                raise GraphV1FlowAuditError("Non-finite JSON number")
        elif kind not in (int, bool, type(None)):
            raise GraphV1FlowAuditError("Value is not an exact JSON primitive")


def _canonical_bytes(value: Any) -> bytes:
    _check_json_tree(value)
    encoder = json.JSONEncoder(
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    )
    chunks: list[bytes] = []
    total = 0
    try:
        for piece in encoder.iterencode(value):
            encoded = piece.encode("utf-8", errors="strict")
            total += len(encoded)
            if total > MAX_JSON_BYTES:
                raise GraphV1FlowAuditError("Canonical JSON exceeds 256 KiB")
            chunks.append(encoded)
    except GraphV1FlowAuditError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise GraphV1FlowAuditError("Value is not canonical UTF-8 JSON") from exc
    return b"".join(chunks)


def audit_graph_v1_flow(graph: Any, labels_manifest: Any) -> GraphV1FlowAudit:
    """Audit restrictive-label flow on an already canonical v1 graph.

    The sidecar shape is exactly:
      {"schema": LABEL_SCHEMA, "graph_id": str, "graph_revision": str,
       "labels": {node_id: "public" | "restricted" | "private"}}

    A flow is flagged only when a more-restricted source feeds a less-restricted
    destination. This is a static advisory check, not a security boundary.
    """
    graph_bytes = _canonical_bytes(graph)
    labels_bytes = _canonical_bytes(labels_manifest)

    if type(graph) is not dict:
        raise GraphV1FlowAuditError("Graph must be an exact JSON object")
    try:
        by_id, _incoming, _order = canonical_graph.validate(graph)
    except (canonical_graph.GraphError, TypeError, ValueError, KeyError) as exc:
        raise GraphV1FlowAuditError("Canonical graph v1 validation failed") from exc

    required = {"schema", "graph_id", "graph_revision", "labels"}
    if type(labels_manifest) is not dict or set(labels_manifest) != required:
        raise GraphV1FlowAuditError("Unsupported visibility sidecar shape")
    if type(labels_manifest["schema"]) is not str or labels_manifest["schema"] != LABEL_SCHEMA:
        raise GraphV1FlowAuditError("Unsupported visibility sidecar schema")
    graph_id = graph.get("id")
    revision = graph.get("revision")
    if (type(labels_manifest["graph_id"]) is not str
            or labels_manifest["graph_id"] != graph_id
            or type(labels_manifest["graph_revision"]) is not str
            or labels_manifest["graph_revision"] != revision):
        raise GraphV1FlowAuditError("Visibility sidecar is stale or belongs to another graph")

    labels = labels_manifest["labels"]
    if type(labels) is not dict:
        raise GraphV1FlowAuditError("Visibility labels must be an object")
    node_ids = set(by_id)
    if set(labels) != node_ids:
        raise GraphV1FlowAuditError("Visibility labels must cover every graph node exactly")
    for node_id, visibility in labels.items():
        if type(node_id) is not str or type(visibility) is not str or visibility not in _LABEL_LEVEL:
            raise GraphV1FlowAuditError("Invalid node visibility label")

    findings: list[FlowViolation] = []
    for index, edge in enumerate(graph["edges"]):
        source_id = edge["from"]["node"]
        target_id = edge["to"]["node"]
        source = labels[source_id]
        target = labels[target_id]
        if _LABEL_LEVEL[source] > _LABEL_LEVEL[target]:
            findings.append(FlowViolation(
                edge_index=index,
                source_visibility=source,
                target_visibility=target,
                code="restricted_data_flows_to_less_restricted_node",
            ))

    return GraphV1FlowAudit(
        schema=REPORT_SCHEMA,
        graph_sha256=hashlib.sha256(graph_bytes).hexdigest(),
        labels_sha256=hashlib.sha256(labels_bytes).hexdigest(),
        node_count=len(by_id),
        edge_count=len(graph["edges"]),
        allowed=not findings,
        violation_count=len(findings),
        violations=tuple(findings),
    )
