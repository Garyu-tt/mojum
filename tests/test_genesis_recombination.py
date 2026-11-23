import copy
import json
import unittest

from mojum.genesis import GENES, GENOTYPE_SCHEMA
from mojum.genesis_recombination import (
    RECOMBINATION_SCHEMA,
    RecombinationError,
    recombine,
    verify_recombination,
)

PARENT_A = {
    "schema": GENOTYPE_SCHEMA,
    "genome_id": "parent-a",
    "revision": "a-r1",
    "inherited": {
        "heartbeat_bpm": 60,
        "body_scale_milli": 1000,
        "sensory_gain_milli": 1000,
    },
}
PARENT_B = {
    "schema": GENOTYPE_SCHEMA,
    "genome_id": "parent-b",
    "revision": "b-r1",
    "inherited": {
        "heartbeat_bpm": 90,
        "body_scale_milli": 1200,
        "sensory_gain_milli": 1500,
    },
}


class RecombinationTests(unittest.TestCase):
    def test_no_crossover_inherits_all_genes_from_starting_parent(self):
        result = recombine(PARENT_A, PARENT_B, child_genome_id="child-1")
        self.assertEqual(result["schema"], RECOMBINATION_SCHEMA)
        self.assertEqual(result["seed"]["inherited"], PARENT_A["inherited"])
        self.assertEqual(set(result["provenance"]["inherited_from"].values()), {"parent-a"})

    def test_single_crossover_switches_parent_after_declared_gene(self):
        result = recombine(
            PARENT_A, PARENT_B, child_genome_id="child-1",
            crossover_after=(1,),
        )
        self.assertEqual(
            result["seed"]["inherited"],
            {
                "heartbeat_bpm": 60,
                "body_scale_milli": 1000,
                "sensory_gain_milli": 1500,
            },
        )
        self.assertEqual(
            result["provenance"]["inherited_from"],
            {
                "heartbeat_bpm": "parent-a",
                "body_scale_milli": "parent-a",
                "sensory_gain_milli": "parent-b",
            },
        )

    def test_starting_parent_can_be_b(self):
        result = recombine(
            PARENT_A, PARENT_B, child_genome_id="child-1",
            starting_parent=1,
        )
        self.assertEqual(result["seed"]["inherited"], PARENT_B["inherited"])

    def test_multiple_crossovers_alternate_segments(self):
        # Three genes admit one interior crossover boundary in this schema.
        result = recombine(
            PARENT_A, PARENT_B, child_genome_id="child-1",
            crossover_after=(1,), starting_parent=1,
        )
        self.assertEqual(result["provenance"]["inherited_from"], {
            "heartbeat_bpm": "parent-b",
            "body_scale_milli": "parent-b",
            "sensory_gain_milli": "parent-a",
        })

    def test_deterministic_result_and_digest(self):
        a = recombine(PARENT_A, PARENT_B, child_genome_id="child-1", crossover_after=(1,))
        b = recombine(copy.deepcopy(PARENT_A), copy.deepcopy(PARENT_B),
                      child_genome_id="child-1", crossover_after=[1])
        self.assertEqual(a, b)
        self.assertEqual(len(a["provenance"]["digest"]), 64)
        self.assertTrue(verify_recombination(a, PARENT_A, PARENT_B))

    def test_inputs_are_not_mutated(self):
        a, b = copy.deepcopy(PARENT_A), copy.deepcopy(PARENT_B)
        before_a, before_b = copy.deepcopy(a), copy.deepcopy(b)
        recombine(a, b, child_genome_id="child-1", crossover_after=(1,))
        self.assertEqual(a, before_a)
        self.assertEqual(b, before_b)

    def test_returned_data_is_detached(self):
        result = recombine(PARENT_A, PARENT_B, child_genome_id="child-1")
        result["seed"]["inherited"]["heartbeat_bpm"] = 180
        result["provenance"]["inherited"]["heartbeat_bpm"] = 180
        self.assertEqual(PARENT_A["inherited"]["heartbeat_bpm"], 60)
        fresh = recombine(PARENT_A, PARENT_B, child_genome_id="child-1")
        self.assertEqual(fresh["seed"]["inherited"]["heartbeat_bpm"], 60)

    def test_child_revision_binds_parent_and_crossover_inputs(self):
        a = recombine(PARENT_A, PARENT_B, child_genome_id="child-1")
        b = recombine(PARENT_A, PARENT_B, child_genome_id="child-1", starting_parent=1)
        self.assertNotEqual(a["seed"]["revision"], b["seed"]["revision"])
        self.assertNotEqual(a["provenance"]["digest"], b["provenance"]["digest"])

    def test_verifier_rejects_tampered_result(self):
        result = recombine(PARENT_A, PARENT_B, child_genome_id="child-1", crossover_after=(1,))
        result["seed"]["inherited"]["heartbeat_bpm"] = 61
        self.assertFalse(verify_recombination(result, PARENT_A, PARENT_B))

    def test_verifier_rejects_wrong_parent(self):
        result = recombine(PARENT_A, PARENT_B, child_genome_id="child-1")
        wrong = copy.deepcopy(PARENT_B)
        wrong["inherited"]["heartbeat_bpm"] = 91
        self.assertFalse(verify_recombination(result, PARENT_A, wrong))

    def test_invalid_starting_parent_rejected(self):
        for value in (True, -1, 2, 0.0, "0"):
            with self.subTest(value=value):
                with self.assertRaises(RecombinationError):
                    recombine(PARENT_A, PARENT_B, child_genome_id="child-1", starting_parent=value)

    def test_invalid_crossover_maps_rejected(self):
        for value in ((True,), (0,), (2,), (1, 1), (1, 0), (1.0,), "1"):
            with self.subTest(value=value):
                with self.assertRaises(RecombinationError):
                    recombine(PARENT_A, PARENT_B, child_genome_id="child-1", crossover_after=value)

    def test_unknown_or_invalid_parent_genes_rejected(self):
        bad = copy.deepcopy(PARENT_B)
        bad["inherited"]["unexpected"] = 3
        with self.assertRaises(RecombinationError):
            recombine(PARENT_A, bad, child_genome_id="child-1")
        bad = copy.deepcopy(PARENT_B)
        bad["inherited"]["heartbeat_bpm"] = True
        with self.assertRaises(RecombinationError):
            recombine(PARENT_A, bad, child_genome_id="child-1")

    def test_parent_identity_and_child_identity_must_be_distinct(self):
        with self.assertRaisesRegex(RecombinationError, "distinct"):
            recombine(PARENT_A, PARENT_A, child_genome_id="child-1")
        with self.assertRaisesRegex(RecombinationError, "new"):
            recombine(PARENT_A, PARENT_B, child_genome_id="parent-a")

    def test_child_identifier_requires_native_utf8_string(self):
        for value in ("", "  ", "\ud800", True, 42):
            with self.subTest(value=ascii(value)):
                with self.assertRaises(RecombinationError):
                    recombine(PARENT_A, PARENT_B, child_genome_id=value)

    def test_parent_requires_native_seed_dict(self):
        class DictSubclass(dict):
            pass
        with self.assertRaises(RecombinationError):
            recombine(DictSubclass(PARENT_A), PARENT_B, child_genome_id="child-1")

    def test_parent_genes_remain_within_canonical_ranges(self):
        result = recombine(PARENT_A, PARENT_B, child_genome_id="child-1", crossover_after=(1,))
        for gene, value in result["seed"]["inherited"].items():
            low, high = GENES[gene]
            self.assertGreaterEqual(value, low)
            self.assertLessEqual(value, high)

    def test_provenance_contains_ordered_parent_hashes_and_gene_order(self):
        result = recombine(PARENT_A, PARENT_B, child_genome_id="child-1")
        provenance = result["provenance"]
        self.assertEqual(provenance["parent_genome_ids"], ["parent-a", "parent-b"])
        self.assertEqual(provenance["gene_order"], list(GENES))
        self.assertEqual(len(provenance["parent_seed_sha256"]), 2)


if __name__ == "__main__":
    unittest.main()
