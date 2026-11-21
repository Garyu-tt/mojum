"""R0.14: private numeric sensory evidence can guide virtual motor intentions.

No objective reward, hidden geometry/oracle, real motors, LLM or consciousness.
The control keeps original goal-free policy; the opt-in mode only filters/
prioritizes actions with empirical observations obtained from a real synthetic
point-room step. Runtime state is reconstructed from one SQLite CAS journal.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError, GUIDANCE_SCHEMA
from mojum.developmental_policy import Explorer, Proprioception, DevelopmentError
from mojum.noosphere import NoosphereError
from mojum.sensory_play import Toy, ToyScene
from mojum.temporal_outcomes import RangeOutcomeMemory
from mojum.world_room import Room, advance

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class SensorGuidedMotorTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.db = Path(d.name) / "guided.sqlite3"
        self.control_db = Path(d.name) / "control.sqlite3"

    def baby(self, path=None, **kwargs):
        return Aquarium(
            self.db if path is None else path, SEED, GRAPH,
            exploration_mode="goal_free", sensory_mode="toy_probe",
            temporal_mode="range_guided", neural_mode="disabled", **kwargs,
        )

    def test_first_unguided_action_and_private_manifest(self):
        with self.baby() as child:
            self.assertEqual(child.temporal_model.updates, 0)
            frame = child.step()
            guidance = frame["motor_guidance"]
            self.assertEqual(guidance["schema"], GUIDANCE_SCHEMA)
            self.assertEqual(guidance["avoid"], [])
            self.assertEqual(guidance["prefer"], [])
            self.assertEqual(guidance["observation"],
                             frame["toy_contact"]["observation"])
            self.assertEqual(set(guidance["predictions"]), {
                "rest", "x+", "x-", "y+", "y-",
            })
            self.assertTrue(all(p["observations"] == 0
                                for p in guidance["predictions"].values()))
            self.assertEqual(child.temporal_model.updates, 1)
            self.assertEqual(child.store.count(), 2)
            self.assertEqual(child.manifest["temporal_mode"], "range_guided")
            self.assertEqual(child.manifest["motor_guidance_schema"],
                             GUIDANCE_SCHEMA)
            self.assertEqual(child.store.dataset()["projection"]["items"], [])
            self.assertNotIn("spectator", str(guidance))

    def test_actual_blocked_virtual_step_produces_veto_and_changes_intention(self):
        # This test constructs genuine sensor + advance() feedback, not an
        # invented blocked flag, then exercises the same live guidance function.
        room = Room(4, 4, ((1.01, 1.96, .01, .08),))
        scene = ToyScene(toys=(
            Toy("experiment.same_side", .80, 2.0, .1, (160, 80, 20), .2),
        ))
        with self.baby(room=room, sensory_scene=scene) as child:
            before = scene.sense(child.world, occluding_obstacles=room.obstacles)
            next_world, result = advance(
                room, child.world, tick=1, dx=.04, dy=0.,
                expected_revision=child.world.revision,
            )
            self.assertTrue(result["blocked"])
            self.assertEqual(result["position"], [1., 2.])
            after = scene.sense(next_world, occluding_obstacles=room.obstacles)
            learned = RangeOutcomeMemory()
            prior = learned.predict(before, "x+")
            learned.observe(before, "x+", after, True, prior)
            # An isolated policy counterfactual. Do NOT commit this injected
            # memory: actual journal tests below separately enforce replay.
            child.temporal_model = learned
            guidance, avoided, preferred = child._motor_guidance()
            self.assertEqual(guidance["observation"], before)
            self.assertIn("x+", avoided)
            self.assertEqual(guidance["predictions"]["x+"]["blocked"], True)
            self.assertNotIn("spectator.same_side", str(guidance))
            sense = Proprioception(
                0, child.world.revision, child.world.x, child.world.y, True,
            )
            untrained_action = child.explorer.decide(sense)
            guided_action = child.explorer.decide(
                sense, avoid=avoided, prefer=preferred,
            )
            self.assertEqual(untrained_action["action"], "x+")
            self.assertNotEqual(guided_action["action"], "x+")
            self.assertLessEqual(abs(guided_action["dx"]) +
                                 abs(guided_action["dy"]), .04)
            self.assertEqual(child.store.count(), 1)

    def test_measured_range_change_prioritizes_another_action(self):
        # Actually advance a visible synthetic scene; a known range change
        # makes x+ preferred over the otherwise innate rest on the first tick.
        room = Room(4, 4)
        scene = ToyScene(toys=(
            Toy("experiment.other", 1.22, 2.0, .1, (200, 30, 40), .5),
        ))
        with self.baby(room=room, sensory_scene=scene) as child:
            state = child.world
            shifted, _ = advance(room, state, tick=1, dx=.04, dy=0.,
                                 expected_revision=state.revision)
            before = scene.sense(state)
            after = scene.sense(shifted)
            # The actual 4cm step crosses a 10cm bucket boundary.
            self.assertNotEqual(before["range_bucket_10cm"],
                                after["range_bucket_10cm"])
            trained = RangeOutcomeMemory()
            trained.observe(before, "x+", after, False,
                            trained.predict(before, "x+"))
            child.temporal_model = trained
            guidance, avoided, preferred = child._motor_guidance()
            self.assertIn("x+", preferred)
            self.assertEqual(guidance["predictions"]["x+"]["blocked"], False)
            cue = Proprioception(0, state.revision, state.x, state.y, False)
            self.assertEqual(child.explorer.decide(cue)["action"], "rest")
            self.assertEqual(
                child.explorer.decide(cue, avoid=avoided, prefer=preferred)["action"],
                "x+",
            )

    def test_ongoing_guided_state_replays_into_same_future_frame(self):
        with self.baby() as original, self.baby(self.control_db) as control:
            ever_known = False
            for _ in range(48):
                left = original.step()
                right = control.step()
                self.assertEqual(left, right)
                self.assertEqual(left["temporal_range"]["before"],
                                 left["toy_contact"]["observation"])
                self.assertEqual(left["motor_guidance"]["observation"],
                                 left["toy_contact"]["observation"])
                if any(p["observations"] > 0 for p in
                       left["motor_guidance"]["predictions"].values()):
                    ever_known = True
            checkpoint = original.checkpoint()
            future = control.step()
            self.assertTrue(ever_known)
            self.assertEqual(original.temporal_model.updates, 48)
            self.assertEqual(original.store.count(), 49)
        with self.baby() as restored:
            self.assertEqual(restored.checkpoint(), checkpoint)
            self.assertEqual(restored.step(), future)
            self.assertEqual(restored.temporal_model.updates, 49)
            self.assertEqual(restored.store.count(), 50)

    def test_committed_history_can_change_later_action_without_external_input(self):
        # This is the critical end-to-end claim: no model injection or
        # privileged movement API. Compare the chosen motor action with
        # the old Explorer policy at the *same previous* RAM/sensory cue.
        # The action must differ only when earlier committed numeric evidence
        # offers a new, unblocked range transition.
        divergence = None
        for i, toy_x in enumerate((1.22, 1.26, 1.18)):
            test_db = self.db.with_name(f"autonomous-{i}.sqlite3")
            toy = ToyScene(toys=(
                Toy("spectator.runtime", toy_x, 2.0, .1, (130, 80, 190), .4),
            ))
            with self.baby(test_db, room=Room(4, 4),
                           sensory_scene=toy) as child:
                for _ in range(160):
                    old_state = child.world
                    old_policy = Explorer.from_snapshot(child.explorer.snapshot())
                    record = child.step()
                    raw = record["interoception"]["heartbeat"]["pulse"]
                    signal = Proprioception(
                        record["tick"], old_state.revision, old_state.x,
                        old_state.y, raw,
                    )
                    baseline = old_policy.decide(signal)["action"]
                    chosen = record["developmental"]["intent"]["action"]
                    if chosen != baseline:
                        evidence = record["motor_guidance"]
                        self.assertGreater(record["tick"], 0)
                        self.assertTrue(evidence["avoid"] or evidence["prefer"])
                        self.assertNotEqual(chosen, "teleport")
                        divergence = (record["tick"], baseline, chosen)
                        break
            if divergence is not None:
                break
        self.assertIsNotNone(
            divergence, "autonomous committed history did not change any motor action",
        )

    def test_guided_actions_independent_of_spectator_ids_and_rgb(self):
        # Both worlds have identical object geometry and movement outcomes.
        # Distinct spectator labels, RGB and hidden softness may change
        # *contact* learning, never the range-based motor guidance decision.
        a_scene = ToyScene(toys=(
            Toy("spectator.one", 1.22, 2., .10, (200, 40, 10), .15),
        ))
        b_scene = ToyScene(toys=(
            Toy("spectator.two", 1.22, 2., .10, (5, 180, 220), .95),
        ))
        with self.baby(sensory_scene=a_scene) as a, self.baby(
                self.control_db, sensory_scene=b_scene) as b:
            for _ in range(32):
                fa, fb = a.step(), b.step()
                self.assertEqual(fa["movement_request"], fb["movement_request"])
                self.assertEqual(fa["developmental"]["intent"],
                                 fb["developmental"]["intent"])
                self.assertEqual(fa["motor_guidance"]["avoid"],
                                 fb["motor_guidance"]["avoid"])
                self.assertEqual(fa["motor_guidance"]["prefer"],
                                 fb["motor_guidance"]["prefer"])
                self.assertEqual(fa["motor_guidance"]["predictions"],
                                 fb["motor_guidance"]["predictions"])
            self.assertEqual(a.temporal_model.snapshot(),
                             b.temporal_model.snapshot())
            self.assertEqual(a.explorer.snapshot(), b.explorer.snapshot())
            self.assertNotEqual(a.sensory_bridge.model.snapshot(),
                                b.sensory_bridge.model.snapshot())
            encoded = json.dumps(a.temporal_model.snapshot())
            self.assertNotIn("spectator", encoded)
            self.assertNotIn("softness", encoded)
            self.assertNotIn("rgb", encoded)

    def test_guided_stale_writer_never_adopts_new_virtual_evidence(self):
        with self.baby() as committed, self.baby() as stale:
            old = stale.temporal_model.snapshot()
            old_policy = stale.explorer.snapshot()
            committed.step()
            with self.assertRaises(NoosphereError):
                stale.step()
            self.assertEqual(stale.temporal_model.snapshot(), old)
            self.assertEqual(stale.explorer.snapshot(), old_policy)
            with self.assertRaises(AquariumError):
                stale.checkpoint()
        with self.baby() as reopened:
            self.assertEqual(reopened.temporal_model.updates, 1)
            self.assertEqual(reopened.explorer.updates, 1)

    def test_guided_cli_resume_and_bad_pair_does_not_create_sqlite(self):
        import os
        import subprocess
        import sys
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        command = [sys.executable, "-m", "mojum.aquarium",
                   "--db", str(self.db), "--ticks", "2",
                   "--exploration-mode", "goal_free",
                   "--sensory-mode", "toy_probe",
                   "--temporal-mode", "range_guided",
                   "--neural-mode", "disabled"]
        one = json.loads(subprocess.check_output(
            command, cwd=ROOT, env=env, text=True, timeout=30,
        ))
        two = json.loads(subprocess.check_output(
            command, cwd=ROOT, env=env, text=True, timeout=30,
        ))
        self.assertEqual(one["temporal_range_state"]["updates"], 2)
        self.assertEqual(two["temporal_range_state"]["updates"], 4)
        self.assertEqual(two["stored_events"], 5)
        invalid_path = self.db.with_name("invalid-cli.sqlite3")
        result = subprocess.run(
            [sys.executable, "-m", "mojum.aquarium", "--db",
             str(invalid_path), "--ticks", "1", "--temporal-mode",
             "range_guided"],
            cwd=ROOT, env=env, capture_output=True, text=True,
            timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires goal_free and toy_probe", result.stderr)
        self.assertFalse(invalid_path.exists())

    def test_forged_preaction_guidance_rejected_before_adoption(self):
        with self.baby() as child:
            payload, _, _ = child._calculate(None, None)
            baseline = child.explorer.snapshot()
            forged = copy.deepcopy(payload)
            forged["motor_guidance"]["avoid"].append("x+")
            with self.assertRaisesRegex(AquariumError, "guidance"):
                child._adopt_developmental(forged)
            self.assertEqual(child.explorer.snapshot(), baseline)
            self.assertEqual(child.store.count(), 1)

    def test_failed_commit_does_not_change_temporal_or_motor_memory(self):
        with self.baby() as child:
            before = child.checkpoint()
            with patch.object(child, "_append_record",
                              side_effect=NoosphereError("injected CAS failure")):
                with self.assertRaises(NoosphereError):
                    child.step()
            self.assertEqual(child.checkpoint(), before)
            self.assertEqual(child.temporal_model.updates, 0)
            self.assertEqual(child.explorer.updates, 0)

    def test_mode_separation_and_api_validation(self):
        with self.baby() as child:
            child.step()
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, exploration_mode="goal_free",
                     sensory_mode="toy_probe", temporal_mode="range_outcome",
                     neural_mode="disabled")
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, exploration_mode="goal_free",
                     sensory_mode="toy_probe", temporal_mode="none",
                     neural_mode="disabled")
        invalid = self.db.with_name("invalid.sqlite3")
        with self.assertRaises(AquariumError):
            Aquarium(invalid, SEED, GRAPH, temporal_mode="range_guided")
        self.assertFalse(invalid.exists())
        with self.assertRaises(DevelopmentError):
            Explorer().decide(
                Proprioception(0, "a"*64, 1, 2, False),
                avoid=(["x+"],),
            )


if __name__ == "__main__":
    unittest.main()
