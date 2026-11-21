"""Acceptance checks for measured range guiding virtual locomotion."""
import json
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.developmental import MOVES
from mojum.noosphere import NoosphereError
from mojum.sensory_play import Toy, ToyScene
from mojum.temporal_outcomes import GUIDANCE_SCHEMA, RangeCue, RangeOutcomeMemory
from mojum.world_room import Room, advance, initial_state


DATA = files("mojum") / "data"
SEED = json.loads((DATA / "genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((DATA / "first_loop.json").read_text(encoding="utf-8"))


class RangeGuidedAquariumTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def aquarium(self, name="guided", *, temporal_mode="range_guided", **kwargs):
        return Aquarium(
            self.root / f"{name}.sqlite3", SEED, GRAPH, neural_mode="disabled",
            motor_mode="developmental_fixture", sensory_mode="toy_probe",
            temporal_mode=temporal_mode, **kwargs,
        )

    def test_committed_range_changes_motion_after_cold_restart(self):
        with self.aquarium(temporal_mode="none", name="control") as control, \
                self.aquarium() as guided, self.aquarium("continuous") as continuous:
            for tick in range(5):
                frame = guided.step()
                self.assertEqual(frame, continuous.step())
                self.assertEqual(frame["developmental_controller"],
                                 control.step()["developmental_controller"])
                if tick == 1:
                    measured = frame["range_guidance"]
                    self.assertEqual(measured["before"]["range_bucket_10cm"], 0)
                    self.assertEqual(measured["after"]["range_bucket_10cm"], 1)
                    self.assertFalse(measured["measured"]["measured_blocked"])
                    self.assertEqual(frame["developmental_controller"]["intent"]["kind"], "north")
            saved = guided.checkpoint()
            self.assertEqual(saved["world_revision"], control.world.revision)
            self.assertEqual(saved["motor_state"], control.motor.snapshot())
            self.assertNotIn("temporal_range_state", control.checkpoint())
            frozen_choice = control.step()["developmental_controller"]["intent"]["kind"]
            expected = continuous.step()
            expected_head = continuous.checkpoint()["journal_head"]

        with self.aquarium(external_expected_head=saved["journal_head"]) as resumed:
            self.assertEqual(resumed.checkpoint(), saved)
            frame = resumed.step()
            self.assertEqual(frame, expected)
            self.assertEqual(frozen_choice, "east")
            self.assertEqual(frame["developmental_controller"]["intent"]["kind"], "north")
            self.assertEqual(frame["movement_request"], {"dx": 0.0, "dy": 0.125})
            evidence = frame["range_guidance"]
            self.assertEqual(evidence["schema"], GUIDANCE_SCHEMA)
            self.assertEqual(evidence["prefer"], ["north"])
            self.assertEqual(evidence["predictions"]["north"], {
                "observations": 1, "next_bucket": 1, "blocked": False,
            })
            self.assertEqual(evidence["before"]["world_revision"], saved["world_revision"])
            self.assertEqual(evidence["after"]["world_revision"], frame["world"]["revision"])
            self.assertEqual(resumed.checkpoint()["journal_head"], expected_head)
            self.assertEqual(resumed.store.count(), 7)

    def test_motor_guidance_uses_range_without_rgb_identity_or_material(self):
        first = ToyScene(toys=(Toy("one", 1.2, 2., .15, (120, 20, 30), .1),))
        second = ToyScene(toys=(Toy("two", 1.2, 2., .15, (40, 30, 20), .9),))
        with self.aquarium("first", sensory_scene=first) as left, \
                self.aquarium("second", sensory_scene=second) as right:
            for _ in range(12):
                a, b = left.step(), right.step()
                self.assertEqual(a["range_guidance"], b["range_guidance"])
                self.assertEqual(a["developmental_controller"], b["developmental_controller"])
            self.assertNotEqual(left.contact_model.snapshot(), right.contact_model.snapshot())
            self.assertEqual(left.range_model.snapshot(), right.range_model.snapshot())
            self.assertEqual(set(a["range_guidance"]["before"]), {
                "tick", "world_revision", "range_bucket_10cm",
            })

    def test_all_predicted_blocked_preserves_attempts_and_scheduled_rest(self):
        room = Room(.16, .16)
        scene = ToyScene(width=.16, height=.16, toys=())
        with self.aquarium(room=room, start=(.08, .08), sensory_scene=scene) as rover:
            frames = [rover.step() for _ in range(24)]
        self.assertEqual(set(frames[4]["range_guidance"]["avoid"]),
                         {move[0] for move in MOVES})
        kinds = [frame["developmental_controller"]["intent"]["kind"] for frame in frames]
        self.assertEqual([tick for tick, kind in enumerate(kinds) if kind == "rest"], [10, 21])
        self.assertEqual(len(frames) - kinds.count("rest"), 22)
        self.assertEqual(sum(frame["world"]["blocked"] for frame in frames), 22)
        self.assertTrue(all(frame["world"]["position"] == [.08, .08] for frame in frames))

    def test_speculation_and_stale_writers_do_not_adopt_range_state(self):
        with self.aquarium() as live, self.aquarium() as stale:
            before = live.checkpoint()
            with patch.object(live.store, "append", side_effect=NoosphereError("simulated failure")):
                with self.assertRaises(NoosphereError):
                    live.step()
            self.assertEqual(live.checkpoint(), before)
            self.assertEqual(live.store.count(), 1)

            live.step()
            with self.assertRaises(NoosphereError):
                stale.step()
            self.assertEqual(stale.range_model.snapshot(), before["temporal_range_state"])
            self.assertEqual(stale.contact_model.snapshot(), before["toy_contact_state"])
            self.assertEqual(stale.world.revision, before["world_revision"])
            self.assertEqual(stale.tick, 0)
            self.assertEqual(live.store.count(), 2)

    def test_rejects_type_forged_guidance_with_valid_sqlite_chain(self):
        with self.aquarium() as rover:
            payload, _, _ = rover._calculate(None, None)
            payload["range_guidance"]["measured"]["measured_blocked"] = 0
            head = rover._append_record(
                "aquarium.frame", "frame-000000000000", payload, 0, rover.genome.revision,
            )
            self.assertEqual(rover.store.verify_chain()["head"], head)
        with self.assertRaisesRegex(AquariumError, "mismatched committed frame"):
            self.aquarium(external_expected_head=head)

    def test_contact_only_journal_refuses_mode_switch(self):
        with self.aquarium(temporal_mode="none") as control:
            control.step()
            saved = control.checkpoint()
        with self.assertRaisesRegex(AquariumError, "mode mismatch"):
            self.aquarium()
        with self.aquarium(temporal_mode="none") as control:
            self.assertEqual(control.checkpoint(), saved)
            self.assertEqual(control.store.count(), 2)

    def test_range_guidance_without_toy_sensor_refused_before_birth(self):
        db = self.root / "invalid.sqlite3"
        with self.assertRaisesRegex(AquariumError, "requires toy_probe"):
            Aquarium(db, SEED, GRAPH, motor_mode="developmental_fixture",
                     temporal_mode="range_guided")
        self.assertFalse(db.exists())


class RangeOutcomeTests(unittest.TestCase):
    def test_equal_counts_for_conflicting_measured_transitions_abstain(self):
        room = Room(4, 4)
        prior = initial_state(room, 1.0, 2.0)
        _, dx, dy = MOVES[0]
        after, measured = advance(room, prior, tick=1, dx=dx, dy=dy,
                                  expected_revision=prior.revision)
        memory = RangeOutcomeMemory()
        outcomes = []
        for x in (1.24, .76):
            scene = ToyScene(toys=(Toy("single", x, 2., .1, (120, 20, 30), .4),))
            before = RangeCue(prior.tick, prior.revision, scene.sense(prior)["range_bucket_10cm"])
            next_cue = RangeCue(after.tick, after.revision, scene.sense(after)["range_bucket_10cm"])
            self.assertEqual(before.range_bucket_10cm, 2)
            outcomes.append(next_cue.range_bucket_10cm)
            memory.observe(before, "east", next_cue, measured["blocked"],
                           memory.predict(before, "east"))
        self.assertEqual(outcomes, [1, 3])
        replayed = RangeOutcomeMemory.from_snapshot(memory.snapshot())
        self.assertEqual(replayed.predict(before, "east"), {
            "observations": 2, "next_bucket": None, "blocked": None,
        })


if __name__ == "__main__":
    unittest.main()
