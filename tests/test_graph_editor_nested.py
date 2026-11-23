"""Hermetic tests for the editor-only nested graph container contract."""
import copy
import json
import unittest
from pathlib import Path

from mojum.graph import GraphError
from mojum.graph_editor_nested import (
    NESTED_SCHEMA, export_nested_editor, import_nested_editor, validate_nested,
)
from mojum.graph_v2 import upgrade_v1

FIXTURE = json.loads((Path(__file__).resolve().parents[1] /
                      "examples" / "first_loop.json").read_text(encoding="utf-8"))


def graph(revision):
    source = copy.deepcopy(FIXTURE)
    source["revision"] = revision
    return upgrade_v1(source, labels={n["id"]: "PUBLIC" for n in source["nodes"]})


def fixture():
    parent, child = graph("parent-r1"), graph("child-r1")
    return parent, [{
        "id": "nested-genesis", "parent_node": "genome",
        "child_graph": child, "position": {"x": 20, "y": 40},
        "portals": [{
            "id": "genome-out", "direction": "output", "node": "genome",
            "port": "genome", "child_node": "genome", "child_port": "genome",
            "type": "genome/1",
        }],
    }]


class NestedGraphEditorTests(unittest.TestCase):
    def test_roundtrip_reactflow_and_rete(self):
        parent, containers = fixture()
        for backend in ("reactflow", "rete"):
            with self.subTest(backend=backend):
                wire = export_nested_editor(parent, containers, backend)
                self.assertEqual(wire["schema"], NESTED_SCHEMA)
                self.assertEqual(len(wire["nodes"]), 1)
                result = import_nested_editor(
                    wire, expected_revision=parent["revision"],
                    expected_sha256=wire["source_sha256"],
                )
                self.assertEqual(result["containers"][0]["id"], "nested-genesis")
                self.assertEqual(result["layout"][0]["position"], {"x": 20, "y": 40})
                self.assertEqual(result["execution"], "NOT_PERFORMED")

    def test_layout_edit_is_detached_and_allowed(self):
        parent, containers = fixture()
        wire = export_nested_editor(parent, containers, "reactflow")
        wire["nodes"][0]["position"] = {"x": -100, "y": 500}
        result = import_nested_editor(
            wire, expected_revision=parent["revision"],
            expected_sha256=wire["source_sha256"],
        )
        self.assertEqual(result["layout"][0]["position"], {"x": -100, "y": 500})
        self.assertEqual(containers[0]["position"], {"x": 20, "y": 40})
        self.assertEqual(result["parent_graph"], parent)

    def test_child_graph_and_portal_edits_fail_closed(self):
        parent, containers = fixture()
        wire = export_nested_editor(parent, containers, "rete")
        wire["nodes"][0]["data"]["child_graph"]["revision"] = "tampered"
        with self.assertRaises(GraphError):
            import_nested_editor(wire, expected_revision=parent["revision"],
                                 expected_sha256=wire["source_sha256"])
        wire = export_nested_editor(parent, containers, "reactflow")
        wire["nodes"][0]["data"]["portals"][0]["type"] = "wrong/1"
        with self.assertRaises(GraphError):
            import_nested_editor(wire, expected_revision=parent["revision"],
                                 expected_sha256=wire["source_sha256"])

    def test_valid_but_replaced_child_graph_fails_source_cas(self):
        parent, containers = fixture()
        wire = export_nested_editor(parent, containers, "reactflow")
        replacement = graph("child-r2")
        wire["nodes"][0]["data"]["child_graph"] = replacement
        with self.assertRaisesRegex(GraphError, "source CAS"):
            import_nested_editor(wire, expected_revision=parent["revision"],
                                 expected_sha256=wire["source_sha256"])

    def test_parent_cas_mismatch_rejected(self):
        parent, containers = fixture()
        wire = export_nested_editor(parent, containers, "reactflow")
        with self.assertRaisesRegex(GraphError, "CAS"):
            import_nested_editor(wire, expected_revision="stale",
                                 expected_sha256=wire["source_sha256"])

    def test_wrong_port_direction_or_type_rejected(self):
        parent, containers = fixture()
        bad = copy.deepcopy(containers)
        bad[0]["portals"][0]["direction"] = "input"
        with self.assertRaises(GraphError):
            validate_nested(parent, bad)
        bad = copy.deepcopy(containers)
        bad[0]["portals"][0]["type"] = "wrong/1"
        with self.assertRaises(GraphError):
            validate_nested(parent, bad)

    def test_duplicate_ids_and_endpoint_aliases_rejected(self):
        parent, containers = fixture()
        bad = copy.deepcopy(containers) * 2
        with self.assertRaises(GraphError):
            validate_nested(parent, bad)
        bad = copy.deepcopy(containers)
        bad[0]["portals"].append(copy.deepcopy(bad[0]["portals"][0]))
        bad[0]["portals"][1]["id"] = "another"
        with self.assertRaises(GraphError):
            validate_nested(parent, bad)

    def test_unknown_parent_and_child_nodes_rejected(self):
        parent, containers = fixture()
        bad = copy.deepcopy(containers)
        bad[0]["parent_node"] = "missing"
        with self.assertRaises(GraphError):
            validate_nested(parent, bad)
        bad = copy.deepcopy(containers)
        bad[0]["portals"][0]["child_node"] = "missing"
        with self.assertRaises(GraphError):
            validate_nested(parent, bad)

    def test_container_and_portal_budgets_are_enforced(self):
        parent, containers = fixture()
        with self.assertRaises(GraphError):
            validate_nested(parent, containers * 65)

    def test_multiple_containers_cannot_claim_one_parent_node(self):
        parent, containers = fixture()
        second = copy.deepcopy(containers[0])
        second["id"] = "another-container"
        with self.assertRaisesRegex(GraphError, "already claimed"):
            validate_nested(parent, containers + [second])

    def test_hostile_python_values_and_invalid_positions_rejected(self):
        parent, containers = fixture()
        for value in (float("nan"), float("inf"), True, "10", 100001, 10 ** 400):
            bad = copy.deepcopy(containers)
            bad[0]["position"]["x"] = value
            with self.subTest(value=repr(value)), self.assertRaises(GraphError):
                validate_nested(parent, bad)
        bad = copy.deepcopy(containers)
        bad[0]["position"]["x"] = object()
        with self.assertRaises(GraphError):
            validate_nested(parent, bad)

    def test_inputs_are_not_mutated(self):
        parent, containers = fixture()
        parent_before, containers_before = copy.deepcopy(parent), copy.deepcopy(containers)
        validate_nested(parent, containers)
        self.assertEqual(parent, parent_before)
        self.assertEqual(containers, containers_before)


if __name__ == "__main__":
    unittest.main()
