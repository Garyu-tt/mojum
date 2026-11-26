"""Acceptance tests for the read-only spinal/Universal-Graph compatibility audit."""
import copy
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO

from mojum.spinal_graph import SpinalError, build_spinal_graph
from tools.spinal_graph_ir_compat import (
    REPORT_SCHEMA,
    audit_spinal_graph_ir_compat,
    main,
)


class SpinalGraphIRCompatibilityTests(unittest.TestCase):
    def test_reports_actual_v1_v2_execution_blockers(self):
        graph = build_spinal_graph(revision=17)
        report = audit_spinal_graph_ir_compat(graph)
        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertEqual(report["revision"], 17)
        self.assertEqual(report["segment_count"], 31)
        self.assertEqual(report["directed_route_count"], 60)
        self.assertEqual(report["bidirectional_feedback_pair_count"], 30)
        self.assertFalse(report["spinal_node_kind_registered_in_v1"])
        self.assertFalse(report["all_spinal_port_types_registered_in_v1"])
        self.assertEqual(report["v2_execution"], "NOT_SUPPORTED")
        self.assertFalse(report["compatible_for_execution"])
        self.assertEqual(
            {item["code"] for item in report["blockers"]},
            {
                "V1_NODE_KIND_NOT_REGISTERED",
                "V1_SPINAL_PORT_TYPES_NOT_REGISTERED",
                "V1_FEEDBACK_CYCLES_FORBIDDEN",
                "V2_EXECUTION_UNSUPPORTED",
            },
        )

    def test_report_is_deterministic_and_does_not_mutate_input(self):
        graph = build_spinal_graph(revision=4)
        before = copy.deepcopy(graph)
        first = audit_spinal_graph_ir_compat(graph)
        second = audit_spinal_graph_ir_compat(graph)
        self.assertEqual(first, second)
        self.assertEqual(graph, before)
        first["blockers"].clear()
        self.assertEqual(len(audit_spinal_graph_ir_compat(graph)["blockers"]), 4)

    def test_rejects_missing_route_instead_of_auditing_partial_topology(self):
        graph = build_spinal_graph()
        graph["edges"].pop()
        with self.assertRaisesRegex(SpinalError, "60 directed"):
            audit_spinal_graph_ir_compat(graph)

    def test_rejects_duplicate_or_nonadjacent_route(self):
        graph = build_spinal_graph()
        graph["edges"][-1] = copy.deepcopy(graph["edges"][0])
        with self.assertRaises(SpinalError):
            audit_spinal_graph_ir_compat(graph)

    def test_rejects_non_native_revision(self):
        graph = build_spinal_graph()
        graph["revision"] = True
        with self.assertRaisesRegex(SpinalError, "revision"):
            audit_spinal_graph_ir_compat(graph)

    def test_cli_emits_one_canonical_json_report(self):
        output = StringIO()
        with redirect_stdout(output):
            report = main(["--revision", "9"])
        self.assertEqual(json.loads(output.getvalue()), report)
        self.assertEqual(report["revision"], 9)
        self.assertEqual(output.getvalue().count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
