"""R0.10 provisional continuity: own observations, no injected object names."""

import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.noosphere import NoosphereError
from mojum.object_memory import (
    ProvisionalObjectMemory, ASSOCIATION_RADIUS_M,
    BEARING_SCHEMA, SCHEMA as MODEL_SCHEMA,
)
from mojum.sensory_play import CuriosityError, SensoryPlay, Toy, ToyScene
from mojum.world_room import advance, initial_state


ROOT = Path(__file__).resolve().parent.parent
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def observation(scene, world):
    sense = scene.sense(world)
    direction = scene.directional_cue(world, sense)
    return sense, direction


class ObservedObjectContinuityTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.path = Path(t.name) / "tracking.sqlite3"

    def open(self, **kw):
        return SensoryPlay(self.path, GRAPH, object_tracking=True, **kw)

    def test_infant_starts_without_object_hypotheses_or_names(self):
        with self.open() as infant:
            self.assertEqual(infant.object_memory.snapshot()["tracks"], {})
            self.assertIsNone(infant.manifest["imported_object_knowledge"])
            self.assertEqual(infant.manifest["object_tracking_schema"], MODEL_SCHEMA)
            f = infant.step()
            self.assertEqual(f["directional_cue"]["schema"], BEARING_SCHEMA)
            self.assertEqual(f["object_hypothesis"]["status"], "new_hypothesis")
            self.assertFalse(f["object_hypothesis"]["identity_verified"])
            self.assertEqual(f["object_hypothesis"]["hypothesis"], "h0001")
            self.assertNotIn("id", f["sensor"])
            self.assertNotIn("id", f["directional_cue"])
            self.assertNotIn("id", f["object_hypothesis"])
            self.assertNotIn("spectator.toy.a", json.dumps(infant.object_memory.snapshot()))
            self.assertEqual(infant.object_memory.accepted_sightings, 1)

    def test_repeated_cues_create_observation_evidence_not_fixed_personality(self):
        with self.open() as infant:
            statuses = []
            for _ in range(60):
                frame = infant.step()
                statuses.append(frame["object_hypothesis"]["status"])
                self.assertFalse(frame["object_hypothesis"]["identity_verified"])
                self.assertEqual(frame["experiment"]["goal"], None)
                self.assertEqual(frame["experiment"]["motor_command"], None)
                self.assertLessEqual(
                    abs(frame["experiment"]["dx"]) +
                    abs(frame["experiment"]["dy"]), 0.040001,
                )
            self.assertIn("new_hypothesis", statuses)
            self.assertIn("continuous_candidate", statuses)
            self.assertGreater(infant.object_memory.accepted_sightings, 10)
            self.assertLessEqual(infant.object_memory.touch_updates, infant.model.updates)
            self.assertTrue(all("cue" in record
                                for record in infant.object_memory.tracks.values()))

    def test_two_objects_with_identical_rgb_can_form_distinct_local_candidates(self):
        rgb = (98, 51, 10)
        scene = ToyScene(toys=(
            Toy("hidden.one", 0.9, 1.0, .12, rgb, .1),
            Toy("hidden.two", 3.1, 3.0, .12, rgb, .9),
        ))
        memory = ProvisionalObjectMemory()
        a = initial_state(scene.room, 0.75, 1)
        sight, direction = observation(scene, a)
        o1 = memory.observe(sight, direction, [a.x, a.y], scene.probe(a, sight))
        self.assertEqual(o1["status"], "new_hypothesis")
        self.assertEqual(o1["hypothesis"], "h0001")
        b, _ = advance(scene.room, a, tick=1, dx=2.2, dy=2.0,
                       expected_revision=a.revision)
        sight2, direction2 = observation(scene, b)
        o2 = memory.observe(sight2, direction2, [b.x, b.y],
                            scene.probe(b, sight2))
        self.assertEqual(o2["status"], "new_hypothesis")
        self.assertEqual(o2["hypothesis"], "h0002")
        self.assertEqual(len(memory.tracks), 2)
        self.assertEqual(memory.tracks["h0001"]["cue"], memory.tracks["h0002"]["cue"])
        self.assertAlmostEqual(memory.tracks["h0001"]["mean_deflection"], .1)
        self.assertAlmostEqual(memory.tracks["h0002"]["mean_deflection"], .9)
        self.assertNotIn("hidden.one", json.dumps(memory.snapshot()))
        self.assertNotIn("hidden.two", json.dumps(memory.snapshot()))

    def test_brief_disappearance_then_reacquisition_is_only_a_hypothesis(self):
        scene = ToyScene(toys=(Toy("hidden.toy", 1.3, 1.0, .13,
                                   (80, 90, 100), .2),))
        memory = ProvisionalObjectMemory()
        a = initial_state(scene.room, 1.0, 1.0)
        s, d = observation(scene, a)
        first = memory.observe(s, d, [a.x, a.y])
        away, _ = advance(scene.room, a, tick=1, dx=2.4, dy=2.5,
                          expected_revision=a.revision)
        s, d = observation(scene, away)
        missing = memory.observe(s, d, [away.x, away.y])
        self.assertEqual(missing["status"], "no_visible_signal")
        back, _ = advance(scene.room, away, tick=2, dx=-2.4, dy=-2.5,
                          expected_revision=away.revision)
        s, d = observation(scene, back)
        later = memory.observe(s, d, [back.x, back.y])
        self.assertEqual(later["status"], "possible_reencounter")
        self.assertEqual(first["hypothesis"], later["hypothesis"])
        self.assertEqual(later["gap_ticks"], 2)
        self.assertFalse(later["identity_verified"])

    def test_two_overlapping_candidates_are_reported_ambiguous_not_merged(self):
        color = (55, 77, 99)
        scene = ToyScene(toys=(
            Toy("hidden.left", 1.0, 2.0, .10, color, .1),
            Toy("hidden.right", 1.5, 2.0, .10, color, .9),
            Toy("hidden.mid", 1.25, 2.0, .10, color, .5),
        ))
        memory = ProvisionalObjectMemory()
        first = initial_state(scene.room, .75, 2)
        sensor, bearing = observation(scene, first)
        # A near-center third fixture may fall in the field; samples must
        # nevertheless derive only from its numeric cue, not its ID.
        memory.observe(sensor, bearing, [first.x, first.y])
        second, _ = advance(scene.room, first, tick=1, dx=1.0, dy=0,
                            expected_revision=first.revision)
        sensor2, bearing2 = observation(scene, second)
        middle = memory.observe(sensor2, bearing2, [second.x, second.y])
        # If the coarse beam already yields an overlap the behavior must
        # be ambiguous; otherwise it must never assert verified identity.
        self.assertFalse(middle["identity_verified"])
        self.assertIn(middle["status"], (
            "new_hypothesis", "continuous_candidate", "ambiguous_identity"
        ))

    def test_directional_sensor_has_no_privileged_labels_or_response(self):
        scene = ToyScene()
        a = initial_state(scene.room, 1, 2)
        s, d = observation(scene, a)
        self.assertEqual(set(d), {
            "schema", "tick", "world_revision", "octant", "source",
        })
        self.assertIs(type(d["octant"]), int)
        self.assertEqual(d["octant"], 0)
        self.assertNotIn("softness", d)
        self.assertNotIn("id", d)
        far, _ = advance(scene.room, a, tick=1, dx=2.6, dy=1.6,
                         expected_revision=a.revision)
        no, no_dir = observation(scene, far)
        self.assertFalse(no["visible"])
        self.assertIsNone(no_dir["octant"])
        with self.assertRaises(CuriosityError):
            scene.directional_cue(a, dict(s, visible=False))
        with self.assertRaises(CuriosityError):
            ProvisionalObjectMemory().observe(
                s, dict(d, hidden_object="secret"), [a.x, a.y]
            )

    def test_fail_closed_on_replayed_or_forged_tracking_observation(self):
        scene = ToyScene()
        a = initial_state(scene.room, 1, 2)
        s, d = observation(scene, a)
        for altered in (
            dict(d, tick=d["tick"]+1),
            dict(d, octant=8),
            dict(d, octant=True),
            dict(d, source="camera"),
            dict(d, world_revision="0"*64),
        ):
            with self.subTest(altered=altered):
                with self.assertRaises(CuriosityError):
                    ProvisionalObjectMemory().observe(s, altered, [a.x, a.y])
        for bad_position in ([float("nan"), 2], [0, True], [2001, 2]):
            with self.subTest(bad_position=bad_position):
                with self.assertRaises(CuriosityError):
                    ProvisionalObjectMemory().observe(s, d, bad_position)
        mem = ProvisionalObjectMemory()
        mem.observe(s, d, [a.x, a.y])
        with self.assertRaises(CuriosityError):
            mem.observe(s, d, [a.x, a.y])

    def test_bounded_object_snapshot_rejects_private_identity_preloading(self):
        scene = ToyScene()
        state = initial_state(scene.room, 1, 2)
        s, d = observation(scene, state)
        mem = ProvisionalObjectMemory()
        mem.observe(s, d, [state.x, state.y], scene.probe(state, s))
        original = mem.snapshot()
        for corrupt in (
            dict(original, next_track=True),
            dict(original, last_tick=True),
            dict(original, accepted_sightings=2),
            dict(original, touch_updates=0),
            dict(original, tracks={"h9999": original["tracks"]["h0001"]}),
            dict(original, tracks={"h0001": dict(
                original["tracks"]["h0001"], mean_deflection=float("nan")
            )}),
            dict(original, tracks={"h0001": dict(
                original["tracks"]["h0001"], cue="unknown.object"
            )}),
        ):
            with self.subTest(corrupt=str(corrupt)[:100]):
                with self.assertRaises(CuriosityError):
                    ProvisionalObjectMemory.from_snapshot(corrupt)
        self.assertEqual(mem.snapshot(), original)

    def test_journal_resume_and_private_replay_do_not_change_hypotheses(self):
        with self.open() as infant:
            frames = [infant.step() for _ in range(60)]
            snap = infant.checkpoint()
            self.assertIn("object_memory", snap)
            self.assertEqual(infant.object_memory.last_tick, 59)
            self.assertEqual(infant.store.count(), 61)
            self.assertEqual(infant.store.dataset()["projection"]["items"], [])
            self.assertEqual(
                infant.store.dataset(include_nonpublic=True, limit=61)["projection"]["items"][-1]["event"]["payload"],
                frames[-1],
            )
        with self.open() as infant:
            self.assertEqual(infant.checkpoint(), snap)
            infant.step()
            after = infant.checkpoint()
        with self.open() as infant:
            self.assertEqual(infant.checkpoint(), after)

    def test_concurrent_writer_conflict_preserves_tracking_memory(self):
        with self.open() as a, self.open() as b:
            old = b.checkpoint()
            a.step()
            with self.assertRaises(NoosphereError):
                b.step()
            self.assertEqual(b.checkpoint(), old)

    def test_legacy_r09_journal_stays_compatible_without_opt_in(self):
        with SensoryPlay(self.path, GRAPH) as legacy:
            first = legacy.step()
            self.assertNotIn("directional_cue", first)
            self.assertNotIn("object_hypothesis", first)
            self.assertNotIn("object_memory", legacy.checkpoint())
        with SensoryPlay(self.path, GRAPH) as legacy:
            self.assertEqual(legacy.tick, 1)
        with self.assertRaises(CuriosityError):
            self.open()

    def test_opt_in_journal_cannot_silently_downgrade_to_r09(self):
        with self.open() as infant:
            infant.step()
        with self.assertRaises(CuriosityError):
            SensoryPlay(self.path, GRAPH)

    def test_cli_opt_in_tracking_is_private_and_resumable(self):
        args = [sys.executable, "-m", "mojum.sensory_play",
                "--db", str(self.path), "--object-tracking", "--ticks", "6",
                "--watch"]
        first = [json.loads(x) for x in subprocess.check_output(args, text=True).splitlines()]
        second = [json.loads(x) for x in subprocess.check_output(args, text=True).splitlines()]
        self.assertEqual([f["tick"] for f in first[:-1]], list(range(6)))
        self.assertEqual([f["tick"] for f in second[:-1]], list(range(6, 12)))
        self.assertIn("object_memory", second[-1]["checkpoint"])
        self.assertFalse(second[0]["object_hypothesis"]["identity_verified"])
        self.assertNotIn("spectator.toy.a", json.dumps(second[0]["object_hypothesis"]))


if __name__ == "__main__":
    unittest.main()
