import copy
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from mojum.genesis import GenomeLedger, ACQUISITION_SCHEMA
from mojum.body_genetics import (
    ASSET_SCHEMA, BODY_SCHEMA, CHROMOSOME_SCHEMA, IDENTITY_SCHEMA,
    BodyContractError, compile_body, select_bassy_asset, validate_bassy_manifest,
    validate_chromosome, validate_human_name, verify_local_glb,
)

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples" / "genesis_seed.json").read_text(encoding="utf-8"))
CHROMOSOME = {
    "schema": CHROMOSOME_SCHEMA,
    "genome_id": SEED["genome_id"],
    "inherited": {
        "height_bias_milli": 1000, "head_ratio_bias_milli": 1000,
        "leg_ratio_bias_milli": 1000, "shoulder_ratio_bias_milli": 1000,
    },
}
IDENTITY = {
    "schema": IDENTITY_SCHEMA, "agent_id": SEED["genome_id"],
    "display_name": "Данте", "assigned_by": "human",
}
GLB_HEADER = b"glTF" + struct.pack("<II", 2, 12)


def manifest(digest=None):
    return {
        "schema": ASSET_SCHEMA, "genome_id": SEED["genome_id"],
        "generator": "Bassy",
        "credits": {
            "text": "Made with Bassy",
            "url": "https://bassycharactercreator.itch.io/bassy-character-creator",
        },
        "stages": [
            {"age_years": 6, "path": "dante/age_06.glb",
             "sha256": digest or "0" * 64, "native_height_mm": 1150,
             "rig_id": "humanoid-bassy-a"},
            {"age_years": 16, "path": "dante/age_16.glb",
             "sha256": "1" * 64, "native_height_mm": 1690,
             "rig_id": "humanoid-bassy-b"},
        ],
    }


class SomaticBodyTests(unittest.TestCase):
    def setUp(self):
        self.ledger = GenomeLedger(SEED)

    def body(self, age=6, chromosome=CHROMOSOME):
        return compile_body(self.ledger.phenotype(0), chromosome, age)

    def test_deterministic_age_growth_and_genome_provenance(self):
        young = self.body(6)
        grown = self.body(16)
        self.assertEqual(young, self.body(6))
        self.assertEqual(young["dimensions"]["height_mm"], 1150)
        self.assertEqual(grown["dimensions"]["height_mm"], 1690)
        self.assertEqual(young["schema"], BODY_SCHEMA)
        self.assertEqual(young["genesis_revision"], self.ledger.revision)
        self.assertEqual(grown["genome_id"], young["genome_id"])
        self.assertNotIn("display_name", young)
        self.assertNotIn("gender", grown)
        self.assertNotIn("appearance", grown)

    def test_genetic_bias_changes_body_not_identity(self):
        other = copy.deepcopy(CHROMOSOME)
        other["inherited"]["height_bias_milli"] = 1150
        self.assertGreater(self.body(10, other)["dimensions"]["height_mm"],
                           self.body(10)["dimensions"]["height_mm"])
        self.assertEqual(self.body(10, other)["genome_id"], SEED["genome_id"])
        self.assertEqual(validate_human_name(IDENTITY, SEED["genome_id"]),
                         IDENTITY)

    def test_genesis_acquisition_changes_scale_prospectively(self):
        a = {
            "schema": ACQUISITION_SCHEMA, "event_id": "g1",
            "genome_id": SEED["genome_id"], "gene": "body_scale_milli",
            "delta": 100, "valid_from_tick": 10,
            "observed_at_ns": 100, "recorded_at_ns": 100,
            "source": "test", "evidence": "synthetic fixture",
        }
        self.ledger.append(a, self.ledger.revision)
        before = compile_body(self.ledger.phenotype(9), CHROMOSOME, 6)
        after = compile_body(self.ledger.phenotype(10), CHROMOSOME, 6)
        self.assertEqual(before["dimensions"]["height_mm"], 1150)
        self.assertEqual(after["dimensions"]["height_mm"], 1265)

    def test_reject_unsupported_age_and_cross_genome_chromosome(self):
        for age in (-1, 5, 17, True, 6.5, "6"):
            with self.assertRaises(BodyContractError):
                self.body(age)
        wrong = copy.deepcopy(CHROMOSOME)
        wrong["genome_id"] = "not-dante"
        with self.assertRaisesRegex(BodyContractError, "another genome"):
            self.body(6, wrong)

    def test_chromosome_is_immutable_and_strict(self):
        original = copy.deepcopy(CHROMOSOME)
        self.body(9)
        self.assertEqual(CHROMOSOME, original)
        for invalid in (None, True, 2000, 0.5):
            wrong = copy.deepcopy(CHROMOSOME)
            wrong["inherited"]["height_bias_milli"] = invalid
            with self.assertRaises(BodyContractError):
                validate_chromosome(wrong)
        wrong = copy.deepcopy(CHROMOSOME)
        wrong["inherited"]["age"] = 6
        with self.assertRaises(BodyContractError):
            validate_chromosome(wrong)

    def test_human_assigned_name_not_genotype(self):
        person = validate_human_name(IDENTITY, SEED["genome_id"])
        self.assertEqual(person["display_name"], "Данте")
        self.assertNotIn("display_name", self.ledger.snapshot()["seed"])
        for bad in ("\n", "   ", "Dante\u200b", ""):
            wrong = dict(IDENTITY, display_name=bad)
            with self.assertRaises(BodyContractError):
                validate_human_name(wrong)
        for bad in ("auto", "model", None):
            with self.assertRaises(BodyContractError):
                validate_human_name(dict(IDENTITY, assigned_by=bad))
        with self.assertRaisesRegex(BodyContractError, "different genome"):
            validate_human_name(IDENTITY, "foreign")


class BassyAssetTests(unittest.TestCase):
    def setUp(self):
        self.body = compile_body(GenomeLedger(SEED).phenotype(0), CHROMOSOME, 10)

    def test_nearest_keyframe_uses_credits_and_scales(self):
        validated = validate_bassy_manifest(manifest())
        selected = select_bassy_asset(self.body, validated)
        self.assertEqual(selected["asset_age_years"], 6)
        self.assertEqual(selected["strategy"], "keyframe_glb_no_mesh_morph")
        self.assertTrue(selected["display_credits"])
        self.assertEqual(selected["credits"]["text"], "Made with Bassy")
        self.assertGreater(selected["scale_milli"], 1000)

    def test_manifest_strictness_and_path_traversal(self):
        for path in ("../escape.glb", "/tmp/dante.glb", "C:/dante.glb",
                     "dante\\x.glb", "dante/.hidden/../x.glb", "x.txt"):
            bad = manifest()
            bad["stages"][0]["path"] = path
            with self.assertRaises(BodyContractError, msg=path):
                validate_bassy_manifest(bad)
        bad = manifest()
        bad["stages"].reverse()
        with self.assertRaises(BodyContractError):
            validate_bassy_manifest(bad)
        bad = manifest()
        bad["credits"]["text"] = "Uncredited"
        with self.assertRaises(BodyContractError):
            validate_bassy_manifest(bad)

    def test_separate_agent_cannot_load_asset(self):
        other = manifest()
        other["genome_id"] = "foreign"
        with self.assertRaisesRegex(BodyContractError, "genome mismatch"):
            select_bassy_asset(self.body, other)

    def test_glb_header_and_checksum_are_checked(self):
        digest = hashlib.sha256(GLB_HEADER).hexdigest()
        m = manifest(digest)
        selected = select_bassy_asset(self.body, m)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "dante" / "age_06.glb"
            p.parent.mkdir()
            p.write_bytes(GLB_HEADER)
            self.assertTrue(verify_local_glb(tmp, selected))
            p.write_bytes(GLB_HEADER + b"extra")
            with self.assertRaisesRegex(BodyContractError, "header"):
                verify_local_glb(tmp, selected)
            p.write_bytes(GLB_HEADER)
            with self.assertRaisesRegex(BodyContractError, "checksum"):
                verify_local_glb(tmp, dict(selected, sha256="f" * 64))

    def test_file_symlink_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            p = Path(tmp) / "dante"
            p.mkdir()
            target = Path(outside) / "age_06.glb"
            target.write_bytes(GLB_HEADER)
            try:
                (p / "age_06.glb").symlink_to(target)
            except OSError:
                self.skipTest("symlink permissions unavailable")
            selected = select_bassy_asset(self.body, manifest())
            with self.assertRaisesRegex(BodyContractError, "escapes asset root"):
                verify_local_glb(tmp, selected)


if __name__ == "__main__":
    unittest.main()
