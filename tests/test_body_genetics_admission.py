import copy
import json
import unittest
from collections import UserDict
from pathlib import Path

from mojum.body_genetics import (
    CHROMOSOME_SCHEMA, BodyContractError, compile_body, validate_chromosome,
)
from mojum.genesis import GenomeLedger

ROOT = Path(__file__).resolve().parent.parent
ROOT_SEED = json.loads(
    (ROOT / "examples" / "genesis_seed.json").read_text(encoding="utf-8")
)
CHROMOSOME = {
    "schema": CHROMOSOME_SCHEMA,
    "genome_id": ROOT_SEED["genome_id"],
    "inherited": {
        "height_bias_milli": 1000,
        "head_ratio_bias_milli": 1000,
        "leg_ratio_bias_milli": 1000,
        "shoulder_ratio_bias_milli": 1000,
    },
}


class BodyGeneticsAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.phenotype = GenomeLedger(ROOT_SEED).phenotype(0)

    def test_accepts_exact_genesis_parameter_contract(self):
        body = compile_body(self.phenotype, CHROMOSOME, 6)
        self.assertEqual(body["dimensions"]["height_mm"], 1150)

    def test_rejects_missing_extra_and_unknown_parameters(self):
        for mutate in (
            lambda p: p.pop("heartbeat_bpm"),
            lambda p: p.update(unexpected=1),
            lambda p: p.update(body_scale_milli=1000, body_scale=1000),
        ):
            bad = copy.deepcopy(self.phenotype)
            mutate(bad["parameters"])
            with self.subTest(parameters=bad["parameters"]):
                with self.assertRaises(BodyContractError):
                    compile_body(bad, CHROMOSOME, 6)

    def test_validates_every_genesis_gene_not_only_body_scale(self):
        invalid_values = (True, False, 0.5, "72", None, 2**63)
        for name in ("heartbeat_bpm", "body_scale_milli", "sensory_gain_milli"):
            for value in invalid_values:
                bad = copy.deepcopy(self.phenotype)
                bad["parameters"][name] = value
                with self.subTest(gene=name, value=value):
                    with self.assertRaises(BodyContractError):
                        compile_body(bad, CHROMOSOME, 6)

    def test_rejects_out_of_range_heartbeat_and_sensory_gain(self):
        for name, value in (("heartbeat_bpm", 29),
                            ("heartbeat_bpm", 181),
                            ("sensory_gain_milli", 99),
                            ("sensory_gain_milli", 2001)):
            bad = copy.deepcopy(self.phenotype)
            bad["parameters"][name] = value
            with self.subTest(gene=name, value=value):
                with self.assertRaises(BodyContractError):
                    compile_body(bad, CHROMOSOME, 6)

    def test_chromosome_validation_returns_detached_nested_copy(self):
        source = copy.deepcopy(CHROMOSOME)
        checked = validate_chromosome(source)
        source["inherited"]["height_bias_milli"] = 1200
        self.assertEqual(checked["inherited"]["height_bias_milli"], 1000)
        checked["inherited"]["height_bias_milli"] = 800
        self.assertEqual(source["inherited"]["height_bias_milli"], 1200)

    def test_rejects_non_native_or_non_json_chromosome_objects_as_domain_errors(self):
        with self.assertRaises(BodyContractError):
            validate_chromosome(UserDict(CHROMOSOME))
        bad = copy.deepcopy(CHROMOSOME)
        bad["inherited"] = UserDict(bad["inherited"])
        with self.assertRaises(BodyContractError):
            validate_chromosome(bad)

    def test_rejects_non_native_phenotype_and_parameter_objects(self):
        with self.assertRaises(BodyContractError):
            compile_body(UserDict(self.phenotype), CHROMOSOME, 6)
        bad = copy.deepcopy(self.phenotype)
        bad["parameters"] = UserDict(bad["parameters"])
        with self.assertRaises(BodyContractError):
            compile_body(bad, CHROMOSOME, 6)

    def test_normal_compilation_is_deterministic_and_inputs_unchanged(self):
        original_phenotype = copy.deepcopy(self.phenotype)
        original_chromosome = copy.deepcopy(CHROMOSOME)
        first = compile_body(self.phenotype, CHROMOSOME, 12)
        second = compile_body(self.phenotype, CHROMOSOME, 12)
        self.assertEqual(first, second)
        self.assertEqual(self.phenotype, original_phenotype)
        self.assertEqual(CHROMOSOME, original_chromosome)


if __name__ == "__main__":
    unittest.main()
