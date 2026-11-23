import copy
import json
import unittest
from pathlib import Path

from mojum.genesis import GenesisError, GenomeLedger, GENES, GENOTYPE_SCHEMA
from mojum.genesis_crossover import RESULT_SCHEMA, cross_genomes

PARENT_A = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "genesis_seed.json").read_text()
)
PARENT_B = copy.deepcopy(PARENT_A)
PARENT_B["genome_id"] = "fixture-parent-b"
PARENT_B["revision"] = "fixture-b-r1"
PARENT_B["inherited"] = {
    "heartbeat_bpm": 90,
    "body_scale_milli": 1250,
    "sensory_gain_milli": 1500,
}


class CrossoverTests(unittest.TestCase):
    def cross(self, a=None, b=None, *, child="fixture-child", seed=12345):
        return cross_genomes(
            copy.deepcopy(PARENT_A if a is None else a),
            copy.deepcopy(PARENT_B if b is None else b),
            child_genome_id=child,
            random_seed=seed,
        )

    def test_schema_and_normal_genome_seed_output(self):
        result = self.cross()
        report = result.to_dict()
        self.assertEqual(report["schema"], RESULT_SCHEMA)
        self.assertEqual(report["seed"]["schema"], GENOTYPE_SCHEMA)
        self.assertEqual(set(report["seed"]["inherited"]), set(GENES))
        self.assertEqual(GenomeLedger(result.seed).snapshot()["seed"], result.seed)
        self.assertTrue(report["seed"]["revision"].startswith("crossover-1-"))

    def test_deterministic_replay(self):
        self.assertEqual(self.cross(seed=87).to_dict(), self.cross(seed=87).to_dict())

    def test_seed_is_part_of_result_identity(self):
        first = self.cross(seed=1).to_dict()
        second = self.cross(seed=2).to_dict()
        self.assertNotEqual(first["random_seed"], second["random_seed"])
        self.assertNotEqual(first["digest"], second["digest"])

    def test_each_locus_records_exact_parent_and_value(self):
        result = self.cross()
        report = result.to_dict()
        parents = {"parent_a": PARENT_A, "parent_b": PARENT_B}
        for locus in report["loci"]:
            parent = parents[locus["source"]]
            self.assertEqual(locus["parent_genome_id"], parent["genome_id"])
            self.assertEqual(locus["value"], parent["inherited"][locus["gene"]])
            self.assertEqual(result.seed["inherited"][locus["gene"]], locus["value"])

    def test_parent_order_is_explicit_in_receipt(self):
        report = self.cross().to_dict()
        self.assertEqual([p["slot"] for p in report["parents"]], ["parent_a", "parent_b"])
        self.assertEqual(report["parents"][0]["genome_id"], PARENT_A["genome_id"])
        self.assertEqual(report["parents"][1]["genome_id"], PARENT_B["genome_id"])

    def test_parent_inputs_are_not_mutated(self):
        a, b = copy.deepcopy(PARENT_A), copy.deepcopy(PARENT_B)
        before_a, before_b = copy.deepcopy(a), copy.deepcopy(b)
        self.cross(a, b)
        self.assertEqual(a, before_a)
        self.assertEqual(b, before_b)

    def test_result_properties_are_detached(self):
        result = self.cross()
        changed = result.seed
        changed["inherited"]["heartbeat_bpm"] = 1
        self.assertNotEqual(result.seed["inherited"]["heartbeat_bpm"], 1)
        result.to_dict()["parents"].clear()
        self.assertEqual(len(result.parents), 2)

    def test_result_digest_recomputes(self):
        from mojum.genesis_crossover import _digest
        result = self.cross().to_dict()
        claimed = result.pop("digest")
        self.assertEqual(claimed, _digest(result))

    def test_child_seed_does_not_copy_acquired_history(self):
        ledger = GenomeLedger(self.cross().seed)
        self.assertEqual(ledger.snapshot()["events"], [])

    def test_invalid_parent_identity_rejected(self):
        b = copy.deepcopy(PARENT_B)
        b["genome_id"] = PARENT_A["genome_id"]
        with self.assertRaises(GenesisError):
            self.cross(PARENT_A, b)
        with self.assertRaises(GenesisError):
            self.cross(child=PARENT_A["genome_id"])

    def test_unknown_or_missing_parent_fields_rejected(self):
        bad = copy.deepcopy(PARENT_A)
        bad["extra"] = "not allowed"
        with self.assertRaises(GenesisError):
            self.cross(bad, PARENT_B)
        bad = copy.deepcopy(PARENT_A)
        del bad["revision"]
        with self.assertRaises(GenesisError):
            self.cross(bad, PARENT_B)

    def test_wrong_schema_and_gene_inventory_rejected(self):
        bad = copy.deepcopy(PARENT_A)
        bad["schema"] = "foreign"
        with self.assertRaises(GenesisError):
            self.cross(bad, PARENT_B)
        bad = copy.deepcopy(PARENT_A)
        bad["inherited"]["not_a_gene"] = 3
        with self.assertRaises(GenesisError):
            self.cross(bad, PARENT_B)

    def test_invalid_gene_values_rejected(self):
        for invalid in (True, 60.0, float("nan"), -1, 10**20):
            bad = copy.deepcopy(PARENT_A)
            bad["inherited"]["heartbeat_bpm"] = invalid
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(GenesisError):
                    self.cross(bad, PARENT_B)

    def test_invalid_random_seed_rejected(self):
        for invalid in (True, -1, 2**64, 1.5):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(GenesisError):
                    self.cross(seed=invalid)

    def test_parent_and_child_ids_are_bounded(self):
        for invalid in ("", " bad", "x" * 129, "child/unsafe", "child\nname"):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(GenesisError):
                    self.cross(child=invalid)

    def test_custom_dict_subclass_rejected(self):
        class HostileDict(dict):
            pass
        with self.assertRaises(GenesisError):
            self.cross(HostileDict(PARENT_A), PARENT_B)

    def test_nested_dict_subclass_rejected(self):
        class HostileDict(dict):
            pass
        bad = copy.deepcopy(PARENT_A)
        bad["inherited"] = HostileDict(bad["inherited"])
        with self.assertRaises(GenesisError):
            self.cross(bad, PARENT_B)

    def test_surrogate_identity_rejected(self):
        bad = copy.deepcopy(PARENT_A)
        bad["genome_id"] = "bad\ud800"
        with self.assertRaises(GenesisError):
            self.cross(bad, PARENT_B)


if __name__ == "__main__":
    unittest.main()
