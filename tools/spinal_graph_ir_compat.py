"""Read-only compatibility audit between the spinal topology and Universal Graph IR.

This module deliberately does not translate or execute the spinal topology.
Graph v1 is a typed DAG with a closed node-kind registry; Graph v2 is audit-only.
"""
from __future__ import annotations

import argparse
import json

from mojum.graph import PORTS, SCHEMA as V1_SCHEMA
from mojum.graph_v2 import V2_SCHEMA
from mojum.spinal_graph import (
    GRAPH_SCHEMA as SPINAL_SCHEMA,
    SpinalError,
    validate_spinal_graph,
    build_spinal_graph,
)

REPORT_SCHEMA = "mojum.spinal-graph-ir-compat-report/1"
SPINAL_PORT_TYPES = frozenset({
    "mojum.spinal-sensory/1",
    "mojum.spinal-intention/1",
    "mojum.spinal-motor/1",
})


def audit_spinal_graph_ir_compat(graph):
    """Return a deterministic compatibility report for a validated spinal graph.

    A successful report means the input topology is valid, not that it can run
    inside Universal Graph IR. The report is intentionally fail-closed and
    never emits a lossy v1 projection.
    """
    normalized = validate_spinal_graph(graph)
    routes = {
        (edge["from"]["segment"], edge["to"]["segment"])
        for edge in normalized["edges"]
    }
    reverse_pairs = sum(
        1 for source, target in routes if (target, source) in routes
    ) // 2
    registered_port_types = {
        port_type
        for inputs, outputs in PORTS.values()
        for port_type in (*inputs.values(), *outputs.values())
    }
    has_spinal_kind = "spinal.segment" in PORTS
    has_spinal_ports = SPINAL_PORT_TYPES.issubset(registered_port_types)

    blockers = [
        {
            "code": "V1_NODE_KIND_NOT_REGISTERED",
            "detail": "Graph v1 has no registered spinal segment node kind.",
        },
        {
            "code": "V1_SPINAL_PORT_TYPES_NOT_REGISTERED",
            "detail": "Graph v1 has no registered spinal sensory, intention and motor port types.",
        },
        {
            "code": "V1_FEEDBACK_CYCLES_FORBIDDEN",
            "detail": "The spinal topology contains bidirectional feedback pairs, while Graph v1 requires a DAG.",
        },
        {
            "code": "V2_EXECUTION_UNSUPPORTED",
            "detail": "Graph v2 is an audit/migration projection and is not executable.",
        },
    ]
    # Keep the report truthful if a future graph release changes one of these
    # capabilities: remove only the blocker whose exact prerequisite is met.
    if has_spinal_kind:
        blockers = [item for item in blockers if item["code"] != "V1_NODE_KIND_NOT_REGISTERED"]
    if has_spinal_ports:
        blockers = [item for item in blockers if item["code"] != "V1_SPINAL_PORT_TYPES_NOT_REGISTERED"]
    if reverse_pairs == 0:
        blockers = [item for item in blockers if item["code"] != "V1_FEEDBACK_CYCLES_FORBIDDEN"]

    return {
        "schema": REPORT_SCHEMA,
        "source_schema": SPINAL_SCHEMA,
        "canonical_v1_schema": V1_SCHEMA,
        "canonical_v2_schema": V2_SCHEMA,
        "revision": normalized["revision"],
        "segment_count": len(normalized["segments"]),
        "directed_route_count": len(normalized["edges"]),
        "bidirectional_feedback_pair_count": reverse_pairs,
        "spinal_node_kind_registered_in_v1": has_spinal_kind,
        "all_spinal_port_types_registered_in_v1": has_spinal_ports,
        "v2_execution": "NOT_SUPPORTED",
        "compatible_for_execution": not blockers,
        "blockers": blockers,
        "safe_action": (
            "Keep the spinal runtime on its explicit versioned contract. "
            "Do not downgrade it to Graph v1 or treat Graph v2 as executable."
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit spinal topology compatibility with Universal Graph IR"
    )
    parser.add_argument("--revision", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        graph = build_spinal_graph(args.revision)
        report = audit_spinal_graph_ir_compat(graph)
    except (SpinalError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return report


if __name__ == "__main__":
    main()
