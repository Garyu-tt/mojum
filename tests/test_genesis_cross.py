import copy
import unittest

from mojum.genesis import GENES, GenesisError, GenomeLedger, GENOTYPE_SCHEMA
from mojum.genesis_cross import cross_seeds, verify_cross_receipt


def parent(genome_id, heartbeat, scale, gain, revision="r1"):
    return {
        "schema": GENOTYPE_SCHEMA,
        "genome_id": genome_id,
        "revision": revision,
        "inherited": {
            "heartbeat_bpm": heartbeat,
            "body_scale_milli": scale,
            "sensory_gain_milli": gain,
        },
    }


PARENT_A = parent("fixture-parent-a", 60, 900, 700)
PARENT_B = parent("fixture-parent-b", 90, 1200, 1500)


class GenesisCrossTests(unittest.TestCase):
    def test_child_is_valid_genesis_v1_seed(self):
        result = cross_seeds(
            PARENT_A, PARENT_B, cross_seed=123, child_genome_id="fixture-child"
        )
        child = result["child_seed"]
        self.assertEqual(child["schema"], GENOTYPE_SCHEMA)
        self.assertEqual(child["genome_id"], "fixture-child")
        self.assertTrue(child["revision"].startswith("cross:"))
        self.assertEqual(set(child["inherited"]), set(GENES))
        ledger = GenomeLedger(child)
        self.assertEqual(ledger.phenotype(0)["parameters"], child["inherited"])

    def test_every_gene_is_selected_from_exactly_one_parent(self):
        result = cross_seeds(
            PARENT_A, PARENT_B, cross_seed=123, child_genome_id="fixture-child"
        )
        for gene, value in result["child_seed"]["inherited"].items():
            self.assertIn(value, (PARENT_A["inherited"][gene], PARENT_B["inherited"][gene]))
            self.assertEqual(value, result["receipt"]["selections"][gene]["value"])
            source = result["receipt"]["selections"][gene]["source"]
            self.assertIn(source, ("parent_a", "parent_b"))
            expected = PARENT_A["inherited"][gene] if source == "parent_a" else PARENT_B["inherited"][gene]
            self.assertEqual(value, expected)

    def test_repeated_call_is_byte_for_byte_deterministic(self):
        first = cross_seeds(
            PARENT_A, PARENT_B, cross_seed=987654, child_genome_id="child"
        )
        second = cross_seeds(
            copy.deepcopy(PARENT_A), copy.deepcopy(PARENT_B),
            cross_seed=987654, child_genome_id="child"
        )
        self.assertEqual(first, second)

    def test_receipt_verifies_and_detects_child_tampering(self):
        result = cross_seeds(
            PARENT_A, PARENT_B, cross_seed=9, child_genome_id="child"
        )
        self.assertTrue(verify_cross_receipt(result))
        changed = copy.deepcopy(result)
        changed["child_seed"]["inherited"]["heartbeat_bpm"] += 1
        with self.assertRaises(GenesisError):
            verify_cross_receipt(changed)

    def test_receipt_detects_selection_tampering(self):
        result = cross_seeds(
            PARENT_A, PARENT_B, cross_seed=9, child_genome_id="child"
        )
        changed = copy.deepcopy(result)
        gene = next(iter(changed["receipt"]["selections"]))
        changed["receipt"]["selections"][gene]["value"] += 1
        with self.assertRaises(GenesisError):
            verify_cross_receipt(changed)

    def test_receipt_rejects_unhashable_selection_source_as_domain_error(self):
        result = cross_seeds(
            PARENT_A, PARENT_B, cross_seed=9, child_genome_id="child"
        )
        changed = copy.deepcopy(result)
        gene = next(iter(changed["receipt"]["selections"]))
        changed["receipt"]["selections"][gene]["source"] = []
        with self.assertRaises(GenesisError):
            verify_cross_receipt(changed)

    def test_parent_seeds_are_not_mutated(self):
        a, b = copy.deepcopy(PARENT_A), copy.deepcopy(PARENT_B)
        before_a, before_b = copy.deepcopy(a), copy.deepcopy(b)
        result = cross_seeds(a, b, cross_seed=4, child_genome_id="child")
        result["child_seed"]["inherited"]["heartbeat_bpm"] = 180
        result["receipt"]["selections"]["heartbeat_bpm"]["value"] = -1
        self.assertEqual(a, before_a)
        self.assertEqual(b, before_b)

    def test_result_mutation_does_not_leak_between_calls(self):
        first = cross_seeds(
            PARENT_A, PARENT_B, cross_seed=1, child_genome_id="child"
        )
        first["child_seed"]["inherited"]["heartbeat_bpm"] = 30
        second = cross_seeds(
            PARENT_A, PARENT_B, cross_seed=1, child_genome_id="child"
        )
        self.assertNotEqual(second["child_seed"]["inherited"]["heartbeat_bpm"], 30)

    def test_same_parent_identity_rejected(self):
        duplicate = parent("same", 60, 900, 700)
        with self.assertRaises(GenesisError):
            cross_seeds(duplicate, copy.deepcopy(duplicate), cross_seed=1, child_genome_id="child")

    def test_child_identity_must_be_new(self):
        for child_id in ("fixture-parent-a", "fixture-parent-b"):
            with self.subTest(child_id=child_id):
                with self.assertRaises(GenesisError):
                    cross_seeds(PARENT_A, PARENT_B, cross_seed=1, child_genome_id=child_id)

    def test_cross_seed_requires_bounded_native_integer(self):
        for seed in (True, False, 1.0, "1", -1, 2**63):
            with self.subTest(seed=seed):
                with self.assertRaises(GenesisError):
                    cross_seeds(PARENT_A, PARENT_B, cross_seed=seed, child_genome_id="child")

    def test_child_identity_rejects_invalid_values(self):
        for child_id in ("", "  ", None, 4, "x" * 129, "\ud800"):
            with self.subTest(child_id=repr(child_id)):
                with self.assertRaises(GenesisError):
                    cross_seeds(PARENT_A, PARENT_B, cross_seed=1, child_genome_id=child_id)

    def test_invalid_parent_genes_fail_closed(self):
        for bad in (True, 60.0, 0, 181):
            with self.subTest(value=bad):
                altered = copy.deepcopy(PARENT_A)
                altered["inherited"]["heartbeat_bpm"] = bad
                with self.assertRaises(GenesisError):
                    cross_seeds(altered, PARENT_B, cross_seed=1, child_genome_id="child")

    def test_child_identity_does_not_change_parent_ledgers(self):
        a = GenomeLedger(PARENT_A)
        b = GenomeLedger(PARENT_B)
        before = (a.revision, b.revision)
        cross_seeds(PARENT_A, PARENT_B, cross_seed=88, child_genome_id="child")
        self.assertEqual((a.revision, b.revision), before)

    def test_parent_order_is_explicitly_part_of_the_input(self):
        ab = cross_seeds(
            PARENT_A, PARENT_B, cross_seed=13, child_genome_id="child"
        )
        ba = cross_seeds(
            PARENT_B, PARENT_A, cross_seed=13, child_genome_id="child"
        )
        self.assertNotEqual(
            ab["receipt"]["parent_a"]["seed_sha256"],
            ba["receipt"]["parent_a"]["seed_sha256"],
        )
        self.assertTrue(verify_cross_receipt(ab))
        self.assertTrue(verify_cross_receipt(ba))


if __name__ == "__main__":
    unittest.main()
