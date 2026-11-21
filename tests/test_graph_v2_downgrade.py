"""Deterministic, verified v2->v1 downgrade for the opt-in graph audit wire.

The downgrade is an interop/audit migration, never an execution path. It must
return the exact canonical v1 graph recorded in provenance, and must fail
closed on tampering, information-flow downgrade or non-v2 input.
"""

import copy
import json
import unittest
from pathlib import Path

from mojum.graph import GraphError, SCHEMA as V1_SCHEMA, validate as validate_v1
from mojum.graph_v2 import V2_SCHEMA, downgrade_v2, upgrade_v1, validate_v2
from mojum.sim import Simulation, digest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "examples" / "first_loop.json").read_text(encoding="utf-8"))


class GraphV2DowngradeTests(unittest.TestCase):
    def test_downgrade_returns_the_exact_canonical_v1_graph(self):
        upgraded = upgrade_v1(FIXTURE)
        self.assertEqual(downgrade_v2(upgraded), FIXTURE)
        self.assertEqual(digest(downgrade_v2(upgraded)), digest(FIXTURE))
        self.assertEqual(validate_v1(downgrade_v2(upgraded))[2],
                         validate_v1(FIXTURE)[2])

    def test_downgrade_is_deterministic_and_does_not_mutate_inputs(self):
        upgraded = upgrade_v1(FIXTURE)
        before = copy.deepcopy(upgraded)
        first = downgrade_v2(upgraded)
        second = downgrade_v2(upgraded)
        self.assertEqual(first, second)
        self.assertEqual(upgraded, before)
        self.assertIsNot(first, second)

    def test_down_converted_graph_runs_identically_to_origin(self):
        original = Simulation(FIXTURE)
        converted = Simulation(downgrade_v2(upgrade_v1(FIXTURE)))
        for _ in range(3):
            self.assertEqual(converted.step(), original.step())
        self.assertEqual(converted.snapshot(), original.snapshot())

    def test_downgrade_strips_only_v2_metadata(self):
        converted = downgrade_v2(upgrade_v1(FIXTURE))
        self.assertEqual(set(converted), {"schema", "id", "revision", "nodes", "edges"})
        self.assertEqual(converted["schema"], V1_SCHEMA)
        for node in converted["nodes"]:
            self.assertEqual(set(node), {"id", "kind", "config", "notes"})

    def test_downgrade_rejects_v1_and_unknown_schemas(self):
        for wrong in (FIXTURE, dict(FIXTURE, schema=V2_SCHEMA), None, []):
            with self.subTest(wrong=type(wrong).__name__):
                with self.assertRaises(GraphError):
                    downgrade_v2(wrong)

    def test_downgrade_rejects_tampered_configuration(self):
        upgraded = upgrade_v1(FIXTURE)
        upgraded["nodes"][0]["config"]["bpm"] = 90
        with self.assertRaisesRegex(GraphError, "provenance checksum"):
            downgrade_v2(upgraded)

    def test_downgrade_rejects_forged_visibility_downgrade(self):
        upgraded = upgrade_v1(FIXTURE)
        next(n for n in upgraded["nodes"] if n["id"] == "genome")["visibility"] = "RESTRICTED"
        next(n for n in upgraded["nodes"] if n["id"] == "heart")["visibility"] = "PUBLIC"
        with self.assertRaisesRegex(GraphError, "visibility downgrade"):
            downgrade_v2(upgraded)

    def test_downgrade_rejects_metadata_that_is_not_purely_additive(self):
        upgraded = upgrade_v1(FIXTURE)
        upgraded["nodes"][0]["note"] = "extra"
        with self.assertRaises(GraphError):
            downgrade_v2(upgraded)

    def test_malformed_v2_nodes_fail_closed_as_graph_error(self):
        # Regression: projection before checking required v2 fields raised
        # KeyError / TypeError instead of the public GraphError contract.
        for name, transform in (
            ("non_mapping_node", lambda g: g["nodes"].__setitem__(0, None)),
            ("missing_visibility", lambda g: g["nodes"][0].pop("visibility")),
            ("missing_ports", lambda g: g["nodes"][0].pop("ports")),
            ("null_ports", lambda g: g["nodes"][0].__setitem__("ports", None)),
        ):
            with self.subTest(name=name):
                upgraded = upgrade_v1(FIXTURE)
                transform(upgraded)
                for operation in (validate_v2, downgrade_v2):
                    with self.subTest(operation=operation.__name__):
                        with self.assertRaises(GraphError):
                            operation(upgraded)

    def test_downgrade_never_returns_a_v2_graph(self):
        converted = downgrade_v2(upgrade_v1(FIXTURE))
        self.assertNotEqual(converted["schema"], V2_SCHEMA)
        self.assertNotIn("origin", converted)


if __name__ == "__main__":
    unittest.main()
