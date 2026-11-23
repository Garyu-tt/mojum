import copy
import unittest

from mojum.graph_v1_flow_audit import (
    GraphV1FlowAuditError,
    audit_graph_v1_flow,
)


def valid_graph():
    nodes = [
        {"id": "seed", "kind": "genesis.seed", "config": {}, "notes": {"purpose": "fixture"}},
        {"id": "body", "kind": "body.heartbeat", "config": {}, "notes": {"purpose": "fixture"}},
        {"id": "sense", "kind": "sensory.interoception", "config": {}, "notes": {"purpose": "fixture"}},
        {"id": "room", "kind": "world.room", "config": {}, "notes": {"purpose": "fixture"}},
        {"id": "brain", "kind": "brain.placeholder", "config": {}, "notes": {"purpose": "fixture"}},
        {"id": "memory", "kind": "memory.episode", "config": {}, "notes": {"purpose": "fixture"}},
    ]
    edges = [
        {"from": {"node": "seed", "port": "genome"}, "to": {"node": "body", "port": "genome"}},
        {"from": {"node": "body", "port": "pulse"}, "to": {"node": "sense", "port": "pulse"}},
        {"from": {"node": "sense", "port": "sensory"}, "to": {"node": "brain", "port": "sensory"}},
        {"from": {"node": "room", "port": "world"}, "to": {"node": "brain", "port": "world"}},
        {"from": {"node": "sense", "port": "sensory"}, "to": {"node": "memory", "port": "sensory"}},
        {"from": {"node": "brain", "port": "observation"}, "to": {"node": "memory", "port": "observation"}},
    ]
    return {"schema": "mechanicus.universal-graph/1", "id": "fixture", "revision": "rev-1",
            "nodes": nodes, "edges": edges}


def labels_for(graph, **overrides):
    labels = {node["id"]: "public" for node in graph["nodes"]}
    labels.update(overrides)
    return {"schema": "mojum.graph.v1.flow-labels/1", "graph_id": graph["id"],
            "graph_revision": graph["revision"], "labels": labels}


class GraphV1FlowAuditTests(unittest.TestCase):
    def test_public_flow_is_advisory_and_allowed(self):
        graph = valid_graph()
        result = audit_graph_v1_flow(graph, labels_for(graph))
        self.assertTrue(result.allowed)
        self.assertEqual(result.violation_count, 0)
        self.assertEqual(result.node_count, 6)
        self.assertEqual(result.edge_count, 6)
        self.assertTrue(result.advisory_only)
        self.assertFalse(result.enforces_runtime_permissions)

    def test_restricted_source_to_public_target_is_reported_redacted(self):
        graph = valid_graph()
        result = audit_graph_v1_flow(graph, labels_for(graph, brain="private"))
        self.assertFalse(result.allowed)
        self.assertEqual(result.violation_count, 1)
        finding = result.violations[0]
        self.assertEqual(finding.edge_index, 5)
        self.assertEqual(finding.source_visibility, "private")
        self.assertEqual(finding.target_visibility, "public")
        self.assertNotIn("brain", str(result.to_dict()))
        self.assertNotIn("memory", str(result.to_dict()))

    def test_flow_to_equal_or_more_restrictive_target_is_allowed(self):
        graph = valid_graph()
        manifest = labels_for(graph, brain="private", memory="private")
        self.assertTrue(audit_graph_v1_flow(graph, manifest).allowed)
        manifest["labels"]["memory"] = "restricted"
        self.assertFalse(audit_graph_v1_flow(graph, manifest).allowed)

    def test_stale_revision_rejected(self):
        graph = valid_graph()
        manifest = labels_for(graph)
        manifest["graph_revision"] = "other"
        with self.assertRaises(GraphV1FlowAuditError):
            audit_graph_v1_flow(graph, manifest)

    def test_wrong_graph_identity_rejected(self):
        graph = valid_graph()
        manifest = labels_for(graph)
        manifest["graph_id"] = "another"
        with self.assertRaises(GraphV1FlowAuditError):
            audit_graph_v1_flow(graph, manifest)

    def test_missing_or_extra_node_labels_rejected(self):
        graph = valid_graph()
        manifest = labels_for(graph)
        del manifest["labels"]["room"]
        with self.assertRaises(GraphV1FlowAuditError):
            audit_graph_v1_flow(graph, manifest)
        manifest = labels_for(graph)
        manifest["labels"]["unknown"] = "public"
        with self.assertRaises(GraphV1FlowAuditError):
            audit_graph_v1_flow(graph, manifest)

    def test_unknown_label_and_non_string_label_rejected(self):
        graph = valid_graph()
        for label in ("secret", 1, None, True):
            with self.subTest(label=label):
                manifest = labels_for(graph)
                manifest["labels"]["seed"] = label
                with self.assertRaises(GraphV1FlowAuditError):
                    audit_graph_v1_flow(graph, manifest)

    def test_wrong_sidecar_schema_and_extra_keys_rejected(self):
        graph = valid_graph()
        manifest = labels_for(graph)
        manifest["schema"] = "mechanicus.universal-graph/1"
        with self.assertRaises(GraphV1FlowAuditError):
            audit_graph_v1_flow(graph, manifest)
        manifest = labels_for(graph)
        manifest["extra"] = "ignored?"
        with self.assertRaises(GraphV1FlowAuditError):
            audit_graph_v1_flow(graph, manifest)

    def test_invalid_canonical_graph_rejected(self):
        graph = valid_graph()
        graph["edges"][0]["from"]["port"] = "missing"
        with self.assertRaises(GraphV1FlowAuditError):
            audit_graph_v1_flow(graph, labels_for(valid_graph()))

    def test_python_only_or_nonfinite_graph_values_rejected(self):
        graph = valid_graph()
        graph["nodes"][0]["config"]["nan"] = float("nan")
        with self.assertRaises(GraphV1FlowAuditError):
            audit_graph_v1_flow(graph, labels_for(valid_graph()))
        graph = valid_graph()
        graph["nodes"][0]["config"]["tuple"] = (1, 2)
        with self.assertRaises(GraphV1FlowAuditError):
            audit_graph_v1_flow(graph, labels_for(valid_graph()))

    def test_native_tree_cardinality_and_text_budgets_are_bounded(self):
        graph = valid_graph()
        graph["oversized_list"] = [None] * 50_001
        with self.assertRaisesRegex(GraphV1FlowAuditError, "item budget"):
            audit_graph_v1_flow(graph, labels_for(valid_graph()))

        graph = valid_graph()
        graph["oversized_mapping"] = {f"extra_{index}": None for index in range(50_001)}
        with self.assertRaisesRegex(GraphV1FlowAuditError, "item budget"):
            audit_graph_v1_flow(graph, labels_for(valid_graph()))

        graph = valid_graph()
        graph["oversized_text"] = "x" * (256 * 1024 + 1)
        with self.assertRaisesRegex(GraphV1FlowAuditError, "256 KiB"):
            audit_graph_v1_flow(graph, labels_for(valid_graph()))

        graph = valid_graph()
        graph["oversized_key"] = "ok"
        graph["k" * (256 * 1024 + 1)] = None
        with self.assertRaisesRegex(GraphV1FlowAuditError, "256 KiB"):
            audit_graph_v1_flow(graph, labels_for(valid_graph()))

        graph = valid_graph()
        graph["aggregate_text"] = ["x" * 140_000, "y" * 140_000]
        with self.assertRaisesRegex(GraphV1FlowAuditError, "256 KiB"):
            audit_graph_v1_flow(graph, labels_for(valid_graph()))

        graph = valid_graph()
        graph["oversized_integer"] = 1 << (256 * 1024 * 4)
        with self.assertRaisesRegex(GraphV1FlowAuditError, "256 KiB"):
            audit_graph_v1_flow(graph, labels_for(valid_graph()))

    def test_hostile_str_subclass_rejected_before_comparison(self):
        class Hostile(str):
            def __eq__(self, other):
                raise RuntimeError("comparison must not run")
            __hash__ = str.__hash__

        graph = valid_graph()
        graph["schema"] = Hostile(graph["schema"])
        with self.assertRaises(GraphV1FlowAuditError):
            audit_graph_v1_flow(graph, labels_for(valid_graph()))

    def test_input_is_not_mutated_and_result_is_deterministic(self):
        graph = valid_graph()
        manifest = labels_for(graph, brain="private")
        before_graph = copy.deepcopy(graph)
        before_manifest = copy.deepcopy(manifest)
        first = audit_graph_v1_flow(graph, manifest)
        second = audit_graph_v1_flow(graph, manifest)
        self.assertEqual(graph, before_graph)
        self.assertEqual(manifest, before_manifest)
        self.assertEqual(first, second)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_key_order_does_not_change_digests(self):
        graph = valid_graph()
        manifest = labels_for(graph)
        reordered_graph = dict(reversed(list(graph.items())))
        reordered_manifest = dict(reversed(list(manifest.items())))
        first = audit_graph_v1_flow(graph, manifest)
        second = audit_graph_v1_flow(reordered_graph, reordered_manifest)
        self.assertEqual(first.graph_sha256, second.graph_sha256)
        self.assertEqual(first.labels_sha256, second.labels_sha256)


if __name__ == "__main__":
    unittest.main()
