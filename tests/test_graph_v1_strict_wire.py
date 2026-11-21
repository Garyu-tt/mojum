"""The executable v1 Graph IR must not silently ignore an incompatible wire shape.

The alternate prototype used the same schema string with visibility/inputs/outputs
and source/target keys. Metadata is not an enforced privacy control.
"""
import copy
import json
import unittest
from pathlib import Path

from mojum.graph import GraphError, validate
from mojum.sim import Simulation, digest


FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "first_loop.json").read_text(encoding="utf-8")
)


class GraphV1StrictWireTests(unittest.TestCase):
    def test_canonical_fixture_keeps_graph_pin_and_replay(self):
        graph = copy.deepcopy(FIXTURE)
        nodes, _, order = validate(graph)
        self.assertEqual(len(nodes), 6)
        self.assertEqual(len(order), 6)
        sim = Simulation(graph)
        self.assertEqual(sim.graph_hash, digest(FIXTURE))
        for _ in range(3):
            sim.step()
        checkpoint = sim.snapshot()
        expected_next = sim.step()
        sim.restore(checkpoint)
        self.assertEqual(sim.step(), expected_next)
        self.assertEqual(graph, FIXTURE)

    def test_ignored_visibility_or_alternate_node_ports_are_rejected(self):
        for extra, value in (
            ("visibility", "PRIVATE"),
            ("visibility", "PUBLIC"),
            ("inputs", {}),
            ("outputs", {}),
            ("source", "external"),
        ):
            with self.subTest(extra=extra, value=value):
                graph = copy.deepcopy(FIXTURE)
                graph["nodes"][0][extra] = value
                with self.assertRaisesRegex(GraphError, "v1 node shape"):
                    validate(graph)

    def test_ignored_source_target_aliases_and_endpoint_metadata_are_rejected(self):
        for extra in ("source", "source_port", "target", "target_port"):
            with self.subTest(extra=extra):
                graph = copy.deepcopy(FIXTURE)
                graph["edges"][0][extra] = "ignored"
                with self.assertRaisesRegex(GraphError, "v1 edge shape"):
                    validate(graph)
        for endpoint in ("from", "to"):
            graph = copy.deepcopy(FIXTURE)
            graph["edges"][0][endpoint]["visibility"] = "PRIVATE"
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(GraphError, "Malformed edge endpoint"):
                    validate(graph)

    def test_unknown_graph_level_policy_is_not_silently_accepted(self):
        graph = copy.deepcopy(FIXTURE)
        graph["visibility"] = "PRIVATE"
        with self.assertRaisesRegex(GraphError, "v1 field"):
            validate(graph)

    def test_purpose_must_be_human_readable_text(self):
        for purpose in ({}, ["test"], 1, True, "   "):
            with self.subTest(purpose=purpose):
                graph = copy.deepcopy(FIXTURE)
                graph["nodes"][0]["notes"]["purpose"] = purpose
                with self.assertRaisesRegex(GraphError, "purpose"):
                    validate(graph)


if __name__ == "__main__":
    unittest.main()
