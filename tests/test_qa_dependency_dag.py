import unittest

from mojum.qa_dependency_dag import SCHEMA, evaluate_dependency_dag


def row(node, state="OPEN", deps=()):
    return {"id": node, "state": state, "depends_on": list(deps)}


class DependencyDagTests(unittest.TestCase):
    def test_dependency_first_order_and_ready_state(self):
        result = evaluate_dependency_dag([
            row("feature", deps=["qa"]),
            row("qa", deps=["base"]),
            row("base", "MERGED"),
        ])
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["topological_order"], ["base", "qa", "feature"])
        self.assertEqual(result["decisions"]["base"]["status"], "TERMINAL")
        self.assertEqual(result["decisions"]["qa"]["status"], "READY")
        self.assertEqual(result["decisions"]["feature"]["status"], "BLOCKED")
        self.assertEqual(result["decisions"]["feature"]["waiting_on"], ["qa"])

    def test_stable_order_independent_of_input_order(self):
        records = [row("z"), row("a"), row("m", deps=["a"])]
        first = evaluate_dependency_dag(records)
        second = evaluate_dependency_dag(list(reversed(records)))
        self.assertEqual(first["topological_order"], second["topological_order"])
        self.assertEqual(first["decisions"], second["decisions"])

    def test_multiple_dependencies_sorted(self):
        result = evaluate_dependency_dag([
            row("target", deps=["b", "a"]),
            row("b"), row("a"),
        ])
        self.assertEqual(result["decisions"]["target"]["waiting_on"], ["a", "b"])

    def test_closed_dependency_blocks_open_child(self):
        result = evaluate_dependency_dag([row("child", deps=["parent"]), row("parent", "CLOSED")])
        self.assertEqual(result["decisions"]["child"]["status"], "BLOCKED")
        self.assertEqual(result["decisions"]["child"]["waiting_on"], ["parent"])

    def test_merged_child_is_terminal_even_with_dependencies(self):
        result = evaluate_dependency_dag([row("done", "MERGED", ["base"]), row("base")])
        self.assertEqual(result["decisions"]["done"]["status"], "TERMINAL")

    def test_empty_graph(self):
        result = evaluate_dependency_dag([])
        self.assertEqual(result["topological_order"], [])
        self.assertEqual(result["node_count"], 0)
        self.assertEqual(result["edge_count"], 0)

    def test_cycle_rejected(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            evaluate_dependency_dag([row("a", deps=["b"]), row("b", deps=["a"])])

    def test_self_edge_rejected(self):
        with self.assertRaisesRegex(ValueError, "self"):
            evaluate_dependency_dag([row("a", deps=["a"])])

    def test_dangling_dependency_rejected(self):
        with self.assertRaisesRegex(ValueError, "dangling"):
            evaluate_dependency_dag([row("a", deps=["missing"])])

    def test_duplicate_node_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate PR"):
            evaluate_dependency_dag([row("a"), row("a")])

    def test_duplicate_edge_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate dependency"):
            evaluate_dependency_dag([row("a", deps=["b", "b"]), row("b")])

    def test_unknown_state_rejected(self):
        with self.assertRaisesRegex(ValueError, "state"):
            evaluate_dependency_dag([row("a", "PENDING")])

    def test_unknown_or_extra_record_fields_rejected(self):
        record = row("a")
        record["head_sha"] = "abc"
        with self.assertRaisesRegex(ValueError, "exactly"):
            evaluate_dependency_dag([record])

    def test_non_string_dependency_rejected(self):
        with self.assertRaisesRegex(ValueError, "dependency id"):
            evaluate_dependency_dag([row("a", deps=[42])])

    def test_boolean_budget_rejected(self):
        with self.assertRaisesRegex(ValueError, "max_nodes"):
            evaluate_dependency_dag([], max_nodes=True)

    def test_node_budget_enforced(self):
        with self.assertRaisesRegex(ValueError, "node budget"):
            evaluate_dependency_dag([row("a"), row("b")], max_nodes=1)

    def test_edge_budget_enforced(self):
        with self.assertRaisesRegex(ValueError, "edge budget"):
            evaluate_dependency_dag([row("a", deps=["b"]), row("b")], max_edges=0)

    def test_budget_cannot_be_raised_above_global_cap(self):
        with self.assertRaisesRegex(ValueError, "max_edges"):
            evaluate_dependency_dag([], max_edges=4001)

    def test_non_sequence_rejected(self):
        with self.assertRaisesRegex(ValueError, "sequence"):
            evaluate_dependency_dag("not records")

    def test_non_mapping_record_rejected(self):
        with self.assertRaisesRegex(ValueError, "mapping"):
            evaluate_dependency_dag([None])


if __name__ == "__main__":
    unittest.main()
