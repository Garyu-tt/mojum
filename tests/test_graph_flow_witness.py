"""Regression tests for opt-in graph-v2 information-flow witness."""

import copy
import hashlib
import json
import unittest

from mojum.graph import GraphError, SCHEMA
from mojum.graph_v2 import upgrade_v1
from mojum.graph_flow_witness import (
    SCHEMA as WITNESS_SCHEMA,
    attest_flow,
)


def graph_v1():
    kinds = {
        "seed": "genesis.seed",
        "beat": "body.heartbeat",
        "sense": "sensory.interoception",
        "room": "world.room",
        "brain": "brain.placeholder",
        "memory": "memory.episode",
    }
    edges = [
        ("seed", "genome", "beat", "genome"),
        ("beat", "pulse", "sense", "pulse"),
        ("sense", "sensory", "brain", "sensory"),
        ("room", "world", "brain", "world"),
        ("sense", "sensory", "memory", "sensory"),
        ("brain", "observation", "memory", "observation"),
    ]
    return {
        "schema": SCHEMA, "id": "public-synthetic", "revision": "r1",
        "nodes": [
            {"id": nid, "kind": kind, "config": {},
             "notes": {"purpose": "synthetic witness fixture"}}
            for nid, kind in kinds.items()
        ],
        "edges": [
            {"from": {"node": a, "port": ap}, "to": {"node": b, "port": bp}}
            for a, ap, b, bp in edges
        ],
    }


def strict_v2(v1=None):
    return upgrade_v1(
        graph_v1() if v1 is None else v1,
        labels={
            "seed": "PUBLIC", "beat": "PUBLIC", "sense": "PRIVATE",
            "room": "PUBLIC", "brain": "PRIVATE", "memory": "RESTRICTED",
        },
    )


class FlowWitnessTests(unittest.TestCase):
    def test_diamond_lineage_sources_and_declared_labels(self):
        witness = attest_flow(strict_v2())
        self.assertEqual(witness["schema"], WITNESS_SCHEMA)
        self.assertEqual(witness["node_count"], 6)
        self.assertEqual(witness["edge_count"], 6)
        self.assertEqual(witness["execution"], "NOT_SUPPORTED")
        self.assertEqual(witness["security"], "labels-only-not-access-control")
        self.assertEqual([node["id"] for node in witness["nodes"]],
                         sorted(["seed", "beat", "sense", "room", "brain", "memory"]))
        entries = {node["id"]: node for node in witness["nodes"]}
        self.assertEqual(entries["seed"]["ancestors"], [])
        self.assertEqual(entries["room"]["source_nodes"], ["room"])
        self.assertEqual(entries["brain"]["ancestors"],
                         ["beat", "room", "seed", "sense"])
        self.assertEqual(entries["memory"]["ancestors"],
                         ["beat", "brain", "room", "seed", "sense"])
        self.assertEqual(entries["memory"]["source_nodes"], ["room", "seed"])
        self.assertEqual(entries["memory"]["declared_visibility"], "RESTRICTED")
        self.assertEqual(entries["memory"]["incoming"], [
            {"from_node": "brain", "from_port": "observation", "to_port": "observation"},
            {"from_node": "sense", "from_port": "sensory", "to_port": "sensory"},
        ])
        self.assertEqual(witness["lineage_pairs"], 12)

    def test_witness_checksum_recomputes_from_canonical_json(self):
        witness = attest_flow(strict_v2())
        digest = witness.pop("witness_sha256")
        encoded = json.dumps(witness, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.assertEqual(digest, hashlib.sha256(encoded).hexdigest())

    def test_input_is_not_mutated(self):
        source = strict_v2()
        frozen = copy.deepcopy(source)
        attest_flow(source)
        self.assertEqual(source, frozen)

    def test_graph_order_changes_hash_but_not_ancestry(self):
        original = graph_v1()
        shuffled = copy.deepcopy(original)
        shuffled["nodes"].reverse()
        shuffled["edges"].reverse()
        a, b = attest_flow(strict_v2(original)), attest_flow(strict_v2(shuffled))
        self.assertNotEqual(a["origin_v1_sha256"], b["origin_v1_sha256"])
        self.assertEqual(a["nodes"], b["nodes"])
        self.assertEqual(a["lineage_pairs"], b["lineage_pairs"])

    def test_never_copies_config_or_notes_to_report(self):
        graph = graph_v1()
        graph["nodes"][0]["config"]["private-test-value"] = "NEVER_EXPORT_THIS"
        graph["nodes"][0]["notes"]["internal"] = "ALSO_NEVER_EXPORT"
        witness = attest_flow(strict_v2(graph))
        text = json.dumps(witness)
        self.assertNotIn("NEVER_EXPORT_THIS", text)
        self.assertNotIn("ALSO_NEVER_EXPORT", text)

    def test_rejects_changed_origin_content(self):
        changed = strict_v2()
        changed["nodes"][0]["config"]["surprise"] = "forged"
        with self.assertRaises(GraphError):
            attest_flow(changed)

    def test_rejects_visibility_downgrade(self):
        changed = strict_v2()
        next(n for n in changed["nodes"] if n["id"] == "brain")[
            "visibility"
        ] = "PUBLIC"
        with self.assertRaises(GraphError):
            attest_flow(changed)

    def test_rejects_v1_and_extra_v2_wire_field(self):
        with self.assertRaises(GraphError):
            attest_flow(graph_v1())
        changed = strict_v2()
        changed["nodes"][0]["untrusted_extension"] = {"secret": 1}
        with self.assertRaises(GraphError):
            attest_flow(changed)

    def test_refuses_report_amplification_from_long_identifiers(self):
        graph = graph_v1()
        graph["nodes"][0]["id"] = "x" * 129
        graph["edges"][0]["from"]["node"] = "x" * 129
        candidate = upgrade_v1(graph, labels={
            "x" * 129: "PUBLIC", "beat": "PUBLIC", "sense": "PRIVATE",
            "room": "PUBLIC", "brain": "PRIVATE", "memory": "RESTRICTED",
        })
        with self.assertRaises(GraphError):
            attest_flow(candidate)

    def test_refuses_invalid_input_before_reading_edges(self):
        for invalid in (None, [], {"nodes": []}, {"nodes": [{}]}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(GraphError):
                    attest_flow(invalid)


if __name__ == "__main__":
    unittest.main()
