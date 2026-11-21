"""GENESIS developmental-clock and human naming journal regression tests."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from mojum.aquarium import Aquarium, AquariumError
from mojum.body_genetics import (
    CHROMOSOME_SCHEMA, IDENTITY_SCHEMA, compile_body_at_month,
)
from mojum.genesis import GenomeLedger

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
SOMATIC = {
    "schema": CHROMOSOME_SCHEMA,
    "genome_id": SEED["genome_id"],
    "inherited": {
        "height_bias_milli": 1000,
        "head_ratio_bias_milli": 1000,
        "leg_ratio_bias_milli": 1000,
        "shoulder_ratio_bias_milli": 1000,
    },
}
IDENTITY = {
    "schema": IDENTITY_SCHEMA,
    "agent_id": SEED["genome_id"],
    "display_name": "Данте",
    "assigned_by": "human",
}


class DevelopmentClockTests(unittest.TestCase):
    def test_intermediate_months_change_shape_without_mutation(self):
        phenotype = GenomeLedger(SEED).phenotype(0)
        lower = compile_body_at_month(phenotype, SOMATIC, 72)
        middle = compile_body_at_month(phenotype, SOMATIC, 78)
        upper = compile_body_at_month(phenotype, SOMATIC, 84)
        self.assertEqual(lower["age_years"], 6)
        self.assertEqual(middle["age_months"], 78)
        self.assertLess(lower["dimensions"]["height_mm"],
                        middle["dimensions"]["height_mm"])
        self.assertLess(middle["dimensions"]["height_mm"],
                        upper["dimensions"]["height_mm"])
        self.assertEqual(lower, compile_body_at_month(phenotype, SOMATIC, 72))
        self.assertNotIn("display_name", middle)
        for invalid in (None, False, 71, 193, "78", 78.5):
            with self.assertRaises(ValueError):
                compile_body_at_month(phenotype, SOMATIC, invalid)

    def test_development_and_name_survive_reopen_without_losing_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "organism.sqlite3"
            with Aquarium(db, SEED, GRAPH, neural_mode="shadow") as agent:
                agent.step(dx=0, dy=0)
                before = agent.checkpoint()
                earlier = agent.body_preview(SOMATIC)
                self.assertEqual(earlier["age_months"], 72)
                self.assertEqual(agent.advance_development(6, reason="virtual semester"), 78)
                self.assertEqual(agent.assign_human_name(IDENTITY, actor_id="owner-human-1"),
                                 IDENTITY)
                self.assertEqual(agent.checkpoint()["development_months"], 78)
                self.assertEqual(agent.checkpoint()["display_name"], "Данте")
                self.assertEqual(agent.checkpoint()["name_provenance"],
                                 "human_declared_unverified")
                later = agent.body_preview(SOMATIC)
                self.assertEqual(later["age_months"], 78)
                self.assertEqual(later["genome_id"], earlier["genome_id"])
                self.assertGreater(later["dimensions"]["height_mm"],
                                   earlier["dimensions"]["height_mm"])
                after = agent.checkpoint()
                for key in ("tick", "genome_revision", "world_revision",
                            "neural_state", "manifest_sha256"):
                    self.assertEqual(before[key], after[key], key)
                self.assertNotEqual(before["journal_head"], after["journal_head"])
                self.assertEqual(agent.body_preview(SOMATIC, age_years=6)["age_years"], 6)
                agent.step(dx=0.01, dy=0)
                final_checkpoint = agent.checkpoint()
            with Aquarium(db, SEED, GRAPH, neural_mode="shadow") as recovered:
                self.assertEqual(recovered.development_months, 78)
                self.assertEqual(recovered.identity, IDENTITY)
                self.assertEqual(recovered.body_preview(SOMATIC)["dimensions"],
                                 later["dimensions"])
                self.assertEqual(recovered.checkpoint(), final_checkpoint)
                recovered.step(dx=0.01, dy=0)
                self.assertEqual(recovered.tick, 3)

    def test_reject_bad_age_and_names_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Aquarium(Path(tmp) / "organism.sqlite3", SEED, GRAPH,
                          neural_mode="disabled") as agent:
                before = agent.checkpoint()
                for amount in (0, -1, 13, True, 1.5, "1"):
                    with self.assertRaises(AquariumError):
                        agent.advance_development(amount)
                for reason in ("", "\u200b", "   ", "\n"):
                    with self.assertRaises(AquariumError):
                        agent.advance_development(1, reason=reason)
                for actor in ("", "   ", "human\u200b", "\n"):
                    with self.assertRaises(AquariumError):
                        agent.assign_human_name(IDENTITY, actor_id=actor)
                not_human = dict(IDENTITY, assigned_by="auto")
                with self.assertRaises(ValueError):
                    agent.assign_human_name(not_human, actor_id="owner")
                self.assertEqual(agent.checkpoint(), before)
                agent.assign_human_name(IDENTITY, actor_id="owner")
                with self.assertRaises(AquariumError):
                    agent.assign_human_name(IDENTITY, actor_id="owner")
                self.assertEqual(agent.development_months, 72)

    def test_maximum_age_is_bounded_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "clock.sqlite3"
            with Aquarium(db, SEED, GRAPH, neural_mode="disabled") as agent:
                for _ in range(10):
                    agent.advance_development(12)
                self.assertEqual(agent.body_preview(SOMATIC)["age_months"], 192)
                before = agent.checkpoint()
                with self.assertRaises(AquariumError):
                    agent.advance_development(1)
                self.assertEqual(agent.checkpoint(), before)
            with Aquarium(db, SEED, GRAPH, neural_mode="disabled") as recovered:
                self.assertEqual(recovered.development_months, 192)
                self.assertEqual(recovered.body_preview(SOMATIC)["age_years"], 16)


if __name__ == "__main__":
    unittest.main()
