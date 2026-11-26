import copy
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from mojum.genesis import GenomeLedger, ACQUISITION_SCHEMA
from mojum.body_genetics import (
    ASSET_SCHEMA, BODY_SCHEMA, CHROMOSOME_SCHEMA, IDENTITY_SCHEMA,
    BodyContractError, compile_body, compile_body_at_month, select_bassy_asset,
    validate_bassy_manifest,
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

    def keyframe_manifest(self, ages=(6, 9, 12, 16)):
        result = manifest()
        result["stages"] = [
            {"age_years": age, "path": f"dante/age_{age:02d}.glb",
             "sha256": f"{age:064x}", "native_height_mm": 1000 + age * 30,
             "rig_id": f"fixture-rig-{age}"}
            for age in ages
        ]
        return result

    def test_monthly_selection_uses_nearest_keyframe_with_younger_ties(self):
        phenotype = GenomeLedger(SEED).phenotype(0)
        for ages in ((6, 9, 12, 16), (6, 16), (9, 12), (6,), (16,),
                     tuple(range(6, 17))):
            assets = self.keyframe_manifest(ages)
            for months in range(72, 193):
                with self.subTest(ages=ages, months=months):
                    # Independent interval oracle: move past a keyframe only
                    # after the midpoint to its successor, retaining ties.
                    expected = ages[0]
                    for younger, older in zip(ages, ages[1:]):
                        if 2 * months > 12 * (younger + older):
                            expected = older
                    body = compile_body_at_month(phenotype, CHROMOSOME, months)
                    selected = select_bassy_asset(body, assets)
                    self.assertEqual(selected["asset_age_years"], expected)

    def test_whole_year_selection_matches_exact_month_boundaries(self):
        phenotype = GenomeLedger(SEED).phenotype(0)
        for assets in (manifest(), self.keyframe_manifest()):
            for age in range(6, 17):
                with self.subTest(ages=[s["age_years"] for s in assets["stages"]],
                                  age=age):
                    whole_year = compile_body(phenotype, CHROMOSOME, age)
                    monthly = compile_body_at_month(phenotype, CHROMOSOME, age * 12)
                    legacy = select_bassy_asset(whole_year, assets)
                    self.assertEqual(select_bassy_asset(monthly, assets), legacy)
                    self.assertEqual(
                        select_bassy_asset(MappingProxyType(whole_year), assets), legacy)

    def test_monthly_mapping_preserves_metadata_and_inputs(self):
        body = compile_body_at_month(GenomeLedger(SEED).phenotype(0), CHROMOSOME, 127)
        body["local_extension"] = {"label": "preview only"}
        assets = self.keyframe_manifest()
        before_body, before_assets = copy.deepcopy(body), copy.deepcopy(assets)
        selected = select_bassy_asset(MappingProxyType(body), assets)
        expected_stage = assets["stages"][2]
        self.assertEqual(selected["asset_age_years"], 12)
        for field in ("path", "sha256", "rig_id"):
            self.assertEqual(selected[field], expected_stage[field])
        self.assertEqual(selected["age_years"], body["age_years"])
        self.assertEqual(selected["genome_id"], body["genome_id"])
        self.assertEqual(selected["schema"], "mojum.avatar.selection/1")
        self.assertEqual(set(selected), {
            "schema", "genome_id", "age_years", "asset_age_years", "path",
            "sha256", "rig_id", "scale_milli", "strategy", "credits", "display_credits",
        })
        self.assertEqual(selected["credits"], assets["credits"])
        self.assertTrue(selected["display_credits"])
        self.assertEqual(selected["strategy"], "keyframe_glb_no_mesh_morph")
        height = body["dimensions"]["height_mm"]
        native = expected_stage["native_height_mm"]
        self.assertEqual(selected["scale_milli"], (height * 1000 + native // 2) // native)
        self.assertEqual(body, before_body)
        self.assertEqual(assets, before_assets)

    def test_reject_invalid_or_inconsistent_optional_months(self):
        body = compile_body_at_month(GenomeLedger(SEED).phenotype(0), CHROMOSOME, 127)
        for months in (None, True, 71, 193, 127.0, "127", 72, 132, 192):
            with self.subTest(months=months), self.assertRaises(BodyContractError):
                select_bassy_asset(dict(body, age_months=months), self.keyframe_manifest())

    def test_journaled_development_selects_monthly_asset_after_cold_replay(self):
        from mojum.aquarium import Aquarium

        graph = json.loads((ROOT / "examples" / "first_loop.json").read_text())
        assets = self.keyframe_manifest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for stage in assets["stages"]:
                path = root / stage["path"]
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(GLB_HEADER)
                stage["sha256"] = hashlib.sha256(GLB_HEADER).hexdigest()
            db = root / "development.sqlite3"
            with Aquarium(db, SEED, graph, neural_mode="shadow") as aquarium:
                aquarium.step(dx=0, dy=0)
                for months, expected in ((91, 9), (127, 12), (169, 16)):
                    while aquarium.development_months < months:
                        aquarium.advance_development(
                            min(12, months - aquarium.development_months))
                    checkpoint = aquarium.checkpoint()
                    body = aquarium.body_preview(CHROMOSOME)
                    selected = select_bassy_asset(body, assets)
                    self.assertEqual(selected["asset_age_years"], expected)
                    self.assertTrue(verify_local_glb(root, selected))
                    self.assertEqual(aquarium.checkpoint(), checkpoint)
            before_bytes = db.read_bytes()
            before_stat = db.stat()
            with Aquarium(db, SEED, graph, neural_mode="shadow", read_only=True) as recovered:
                self.assertEqual(recovered.checkpoint(), checkpoint)
                self.assertEqual(recovered.body_preview(CHROMOSOME), body)
                self.assertEqual(select_bassy_asset(recovered.body_preview(CHROMOSOME),
                                                   assets), selected)
                self.assertTrue(verify_local_glb(root, selected))
                self.assertEqual(recovered.checkpoint(), checkpoint)
            self.assertEqual(db.read_bytes(), before_bytes)
            self.assertEqual(db.stat().st_mtime_ns, before_stat.st_mtime_ns)
            self.assertEqual(db.stat().st_mode, before_stat.st_mode)

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

    def test_aquarium_body_preview_does_not_modify_journal(self):
        from mojum.aquarium import Aquarium

        graph = json.loads((ROOT / "examples" / "first_loop.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            with Aquarium(Path(tmp) / "preview.sqlite3", SEED, graph,
                          neural_mode="disabled") as aquarium:
                before = aquarium.checkpoint()
                p = aquarium.body_preview(CHROMOSOME, age_years=6)
                self.assertEqual(before, aquarium.checkpoint())
                self.assertEqual(p["genome_id"], aquarium.agent_id)
                self.assertEqual(p["dimensions"]["height_mm"], 1150)
                aquarium.step(dx=0, dy=0)
                followup = aquarium.body_preview(CHROMOSOME, age_years=16)
                self.assertEqual(followup["at_tick"], aquarium.tick)
                self.assertEqual(followup["genome_id"], p["genome_id"])

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
