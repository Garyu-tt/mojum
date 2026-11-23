"""Hermetic redaction, compatibility and ordering regressions for graph revision diff."""

import copy
import json
import unittest
from pathlib import Path

from mojum.graph import GraphError
from mojum.graph_v2 import upgrade_v1
from mojum.graph_revision_diff import compare_revisions


FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "first_loop.json")
    .read_text(encoding="utf-8")
)


class GraphRevisionDiffTests(unittest.TestCase):
    def test_identical_graph_is_noop_and_inputs_not_mutated(self):
        before = copy.deepcopy(FIXTURE)
        after = copy.deepcopy(FIXTURE)
        snapshot = copy.deepcopy(before)
        result = compare_revisions(before, after)
        self.assertEqual(before, snapshot)
        self.assertEqual(after, snapshot)
        self.assertFalse(result["wire_changed"])
        self.assertFalse(result["revision_changed"])
        self.assertFalse(result["semantic_changed"])
        self.assertTrue(all(count == 0 for count in result["change_counts"].values()))
        self.assertEqual(result["before_sha256"], result["after_sha256"])
        self.assertEqual(result["execution"], "NOT_PERFORMED")

    def test_node_and_edge_reordering_is_not_semantic_change(self):
        after = copy.deepcopy(FIXTURE)
        after["nodes"].reverse()
        after["edges"].reverse()
        result = compare_revisions(FIXTURE, after)
        self.assertTrue(result["wire_changed"])
        self.assertFalse(result["revision_changed"])
        self.assertFalse(result["semantic_changed"])
        self.assertEqual(
            result["before_semantic_sha256"], result["after_semantic_sha256"]
        )
        self.assertFalse(any(result["change_counts"].values()))

    def test_add_and_remove_isolated_sink_counts_nodes_and_edges(self):
        after = copy.deepcopy(FIXTURE)
        sink = copy.deepcopy(next(node for node in after["nodes"]
                                  if node["id"] == "episode"))
        sink["id"] = "additional-sink"
        after["nodes"].append(sink)
        after["edges"].extend([
            {"from": {"node": "sense", "port": "sensory"},
             "to": {"node": "additional-sink", "port": "sensory"}},
            {"from": {"node": "brain", "port": "observation"},
             "to": {"node": "additional-sink", "port": "observation"}},
        ])
        added = compare_revisions(FIXTURE, after)["change_counts"]
        self.assertEqual(added["nodes_added"], 1)
        self.assertEqual(added["edges_added"], 2)
        self.assertEqual(added["nodes_removed"], 0)
        removed = compare_revisions(after, FIXTURE)["change_counts"]
        self.assertEqual(removed["nodes_removed"], 1)
        self.assertEqual(removed["edges_removed"], 2)

    def test_revision_only_and_nested_config_changes_distinct(self):
        after = copy.deepcopy(FIXTURE)
        after["revision"] = "fixture-next"
        revision_only = compare_revisions(FIXTURE, after)
        self.assertTrue(revision_only["revision_changed"])
        self.assertFalse(revision_only["semantic_changed"])
        after["nodes"][0]["config"]["secret_token"] = "PRIVATE_DO_NOT_DISCLOSE"
        result = compare_revisions(FIXTURE, after)
        self.assertTrue(result["semantic_changed"])
        self.assertEqual(result["change_counts"]["node_config_changed"], 1)
        self.assertNotIn("PRIVATE_DO_NOT_DISCLOSE", json.dumps(result))
        self.assertNotIn("genome", json.dumps(result))
        self.assertNotIn(FIXTURE["id"], json.dumps(result))

    def test_json_type_changes_do_not_hide_behind_bool_integer_equality(self):
        before = copy.deepcopy(FIXTURE)
        before["nodes"][0]["config"]["enabled"] = 1
        after = copy.deepcopy(before)
        # Python considers True == 1; the JSON wire intentionally does not.
        after["nodes"][0]["config"]["enabled"] = True
        result = compare_revisions(before, after)
        self.assertEqual(result["change_counts"]["node_config_changed"], 1)
        self.assertTrue(result["semantic_changed"])

    def test_note_content_changes_but_no_values_leak(self):
        after = copy.deepcopy(FIXTURE)
        after["nodes"][0]["notes"]["purpose"] = "PRIVATE_UNRELEASED_MESSAGE"
        result = compare_revisions(FIXTURE, after)
        self.assertEqual(result["change_counts"]["node_notes_changed"], 1)
        self.assertNotIn("PRIVATE_UNRELEASED_MESSAGE", json.dumps(result))

    def test_v2_visibility_changed_is_metadata_only(self):
        before = upgrade_v1(FIXTURE)
        after = upgrade_v1(FIXTURE, labels={"episode": "RESTRICTED"})
        result = compare_revisions(before, after)
        self.assertEqual(result["change_counts"]["node_visibility_changed"], 1)
        self.assertTrue(result["semantic_changed"])
        self.assertEqual(result["execution"], "NOT_PERFORMED")
        self.assertNotIn("episode", json.dumps(result))
        self.assertNotIn("RESTRICTED", json.dumps(result))

    def test_v2_provenance_tamper_fails_closed(self):
        before = upgrade_v1(FIXTURE)
        after = copy.deepcopy(before)
        after["origin"]["sha256"] = "0" * 64
        with self.assertRaises(GraphError):
            compare_revisions(before, after)
        after = copy.deepcopy(before)
        after["nodes"][0]["config"]["bpm"] = 100
        with self.assertRaises(GraphError):
            compare_revisions(before, after)

    def test_different_id_and_mixed_schema_rejected(self):
        other = copy.deepcopy(FIXTURE)
        other["id"] = "different-graph"
        with self.assertRaisesRegex(GraphError, "identities"):
            compare_revisions(FIXTURE, other)
        with self.assertRaisesRegex(GraphError, "mixed graph schemas"):
            compare_revisions(FIXTURE, upgrade_v1(FIXTURE))

    def test_shape_and_user_defined_equality_spoof_rejected(self):
        wrong = copy.deepcopy(FIXTURE)
        wrong["edges"][0]["source"] = "alias"
        with self.assertRaises(GraphError):
            compare_revisions(FIXTURE, wrong)

        class SpoofDict(dict):
            def __eq__(self, other):
                return True

        wrong = copy.deepcopy(FIXTURE)
        wrong["nodes"][0]["config"] = SpoofDict({"bpm": 60})
        with self.assertRaisesRegex(GraphError, "exact JSON"):
            compare_revisions(FIXTURE, wrong)

    def test_json_limit_surrogates_nonfinite_and_recursion(self):
        for bad in ("X" * (300 * 1024), "\ud800", float("nan"),
                    float("inf"), (1, 2)):
            with self.subTest(bad_type=type(bad).__name__):
                wrong = copy.deepcopy(FIXTURE)
                wrong["nodes"][0]["notes"]["secret"] = bad
                with self.assertRaises(GraphError):
                    compare_revisions(FIXTURE, wrong)
        wrong = copy.deepcopy(FIXTURE)
        nested = []
        top = nested
        for _ in range(140):
            child = []
            nested.append(child)
            nested = child
        wrong["nodes"][0]["notes"]["deep"] = top
        with self.assertRaisesRegex(GraphError, "complexity"):
            compare_revisions(FIXTURE, wrong)

    def test_same_graph_v2_permutations_have_same_semantic_digest(self):
        before = upgrade_v1(FIXTURE, labels={"genome": "PUBLIC"})
        reordered = copy.deepcopy(FIXTURE)
        reordered["nodes"].reverse()
        reordered["edges"].reverse()
        # A valid v2 provenance SHA is tied to the original v1 ordering.
        after = upgrade_v1(reordered, labels={"genome": "PUBLIC"})
        result = compare_revisions(before, after)
        self.assertFalse(result["semantic_changed"])
        self.assertTrue(result["wire_changed"])


if __name__ == "__main__":
    unittest.main()
