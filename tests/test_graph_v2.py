"""Compatibility and non-execution acceptance for the distinct graph-v2 wire."""

import copy
import json
import unittest
from pathlib import Path

from mojum.graph import GraphError, SCHEMA as V1_SCHEMA, validate
from mojum.graph_v2 import V2_SCHEMA, upgrade_v1, validate_v2
from mojum.sim import Simulation, digest

FIXTURE = json.loads((Path(__file__).resolve().parent.parent / "examples" / "first_loop.json").read_text(encoding="utf-8"))


class GraphV2Tests(unittest.TestCase):
    def test_upgrade_is_deterministic_lossless_and_private_by_default(self):
        old = copy.deepcopy(FIXTURE)
        old_sha = digest(old)
        first = upgrade_v1(old)
        self.assertEqual(old, FIXTURE)
        self.assertEqual(digest(old), old_sha)
        self.assertEqual(first, upgrade_v1(old))
        self.assertEqual(first["schema"], V2_SCHEMA)
        self.assertEqual(first["origin"], {"schema": V1_SCHEMA, "sha256": old_sha})
        self.assertTrue(all(n["visibility"] == "PRIVATE" for n in first["nodes"]))
        audit = validate_v2(first)
        self.assertEqual(audit["origin_sha256"], old_sha)
        self.assertEqual(audit["execution"], "NOT_SUPPORTED")
        self.assertEqual(audit["node_count"], 6)
        self.assertEqual(audit["edge_count"], 6)

    def test_legacy_runner_and_replay_remain_unchanged(self):
        old = Simulation(FIXTURE)
        old.step()
        snapshot = old.snapshot()
        upgrade_v1(FIXTURE)
        replay = Simulation(FIXTURE)
        replay.restore(snapshot)
        self.assertEqual(replay.step(), old.step())
        with self.assertRaisesRegex(GraphError, "graph schema"):
            Simulation(upgrade_v1(FIXTURE))

    def test_ports_are_explicit_and_typed_from_canonical_registry(self):
        upgraded = upgrade_v1(FIXTURE)
        sense = next(n for n in upgraded["nodes"] if n["id"] == "sense")
        self.assertEqual(sense["ports"]["inputs"], {"pulse": "heartbeat/1"})
        self.assertEqual(sense["ports"]["outputs"], {"sensory": "sensory/1"})
        sense["ports"]["inputs"]["pulse"] = "any"
        with self.assertRaisesRegex(GraphError, "ports"):
            validate_v2(upgraded)

    def test_mixed_version_and_incompatible_v1_shape_fail_closed(self):
        bad = copy.deepcopy(FIXTURE)
        bad["nodes"][0] = {"id": "genome", "visibility": "PRIVATE", "inputs": {}, "outputs": {"genome": "genome/1"}}
        with self.assertRaises(GraphError):
            upgrade_v1(bad)
        with self.assertRaises(GraphError):
            validate_v2(FIXTURE)
        with self.assertRaises(GraphError):
            validate(upgrade_v1(FIXTURE))

    def test_explicit_low_to_high_flow_allowed(self):
        audited = upgrade_v1(FIXTURE, labels={"genome": "PUBLIC", "heart": "PRIVATE"})
        self.assertEqual(validate_v2(audited)["node_count"], 6)

    def test_high_to_low_flow_rejected_not_silently_declassified(self):
        with self.assertRaisesRegex(GraphError, "visibility downgrade"):
            upgrade_v1(FIXTURE, labels={"genome": "RESTRICTED", "heart": "PUBLIC"})
        graph = upgrade_v1(FIXTURE)
        next(n for n in graph["nodes"] if n["id"] == "genome")["visibility"] = "RESTRICTED"
        next(n for n in graph["nodes"] if n["id"] == "heart")["visibility"] = "PRIVATE"
        with self.assertRaisesRegex(GraphError, "visibility downgrade"):
            validate_v2(graph)

    def test_provenance_rejects_tampered_configuration(self):
        upgraded = upgrade_v1(FIXTURE)
        upgraded["nodes"][0]["config"]["bpm"] = 90
        with self.assertRaisesRegex(GraphError, "provenance checksum"):
            validate_v2(upgraded)

    def test_invalid_origin_and_reserved_v1_keys_rejected(self):
        graph = upgrade_v1(FIXTURE)
        graph["origin"]["sha256"] = "G" * 64
        with self.assertRaisesRegex(GraphError, "provenance"):
            validate_v2(graph)
        original = copy.deepcopy(FIXTURE)
        original["nodes"][0]["visibility"] = "PRIVATE"
        # Strict v1 validation may reject the node shape before the adapter
        # reaches its reserved-metadata check; both paths must fail closed.
        with self.assertRaises(GraphError):
            upgrade_v1(original)

    def test_strict_labels_and_unknown_node(self):
        for labels in ({"absent": "PRIVATE"}, {"genome": "public"}, {"genome": 1}, []):
            with self.subTest(labels=labels), self.assertRaises(GraphError):
                upgrade_v1(FIXTURE, labels=labels)

    def test_invalid_json_values_and_unicode_rejected(self):
        for wrong in (float("nan"), (1, 2), "\ud800"):
            with self.subTest(wrong=repr(wrong)):
                source = copy.deepcopy(FIXTURE)
                source["nodes"][0]["notes"]["bad"] = wrong
                with self.assertRaises(GraphError):
                    upgrade_v1(source)

    def test_no_assumption_that_sha_is_authentication(self):
        # A new SHA may be recomputed by any local writer. The report is not a
        # signature, permission grant or security enforcement mechanism.
        graph = upgrade_v1(FIXTURE)
        self.assertEqual(validate_v2(graph)["security"], "labels-only-not-access-control")


if __name__ == "__main__":
    unittest.main()
