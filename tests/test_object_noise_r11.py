"""R0.11 optional RGB jitter tolerance and atomic memory-capacity tests.

All scenarios are numeric fixtures, without pretrained identity labels.
"""

import json
from pathlib import Path
import tempfile
import unittest

from mojum.object_memory import (
    BEARING_SCHEMA, MAX_TRACKS, ProvisionalObjectMemory,
)
from mojum.sensory_play import (
    CuriosityError, SensoryPlay, SENSE_SCHEMA,
)


ROOT = Path(__file__).resolve().parent.parent
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
SHA = "a" * 64


def numeric_frame(tick, color):
    sensor = {
        "schema": SENSE_SCHEMA,
        "tick": tick,
        "world_revision": SHA,
        "visible": True,
        "reflectance_rgb": list(color),
        "range_bucket_10cm": 1,
        "source": "synthetic_local_proximity_not_camera",
    }
    direction = {
        "schema": BEARING_SCHEMA,
        "tick": tick,
        "world_revision": SHA,
        "octant": 0,
        "source": "synthetic_direction_quantization_not_camera",
    }
    return sensor, direction


class BoundedToleranceTests(unittest.TestCase):
    def test_legacy_snapshot_is_unchanged_when_tolerance_disabled(self):
        plain = ProvisionalObjectMemory()
        self.assertNotIn("rgb_tolerance_l1", plain.snapshot())
        self.assertNotIn("contact_guard", plain.snapshot())
        self.assertEqual(ProvisionalObjectMemory.from_snapshot(
            plain.snapshot()).snapshot(), plain.snapshot())

    def test_tolerance_requires_nonboolean_integer_in_small_fixed_range(self):
        for invalid in (-1, 7, True, False, 1.5, "1", None):
            with self.subTest(invalid=invalid):
                if invalid is False:
                    continue  # bool False rejected as value, tested below
                with self.assertRaises(CuriosityError):
                    ProvisionalObjectMemory(rgb_tolerance=invalid)
        with self.assertRaises(CuriosityError):
            ProvisionalObjectMemory(rgb_tolerance=False)
        for good in (0, 1, 2, 6):
            self.assertEqual(ProvisionalObjectMemory(
                rgb_tolerance=good).rgb_tolerance, good)

    def test_snapshot_pins_tolerance_and_rejects_forged_optional_field(self):
        m = ProvisionalObjectMemory(contact_guard=True, rgb_tolerance=1)
        snapshot = m.snapshot()
        self.assertEqual(snapshot["rgb_tolerance_l1"], 1)
        self.assertEqual(snapshot["contact_guard"], True)
        self.assertEqual(
            ProvisionalObjectMemory.from_snapshot(snapshot).snapshot(),
            snapshot,
        )
        for corrupt in (
            dict(snapshot, rgb_tolerance_l1=0),
            dict(snapshot, rgb_tolerance_l1=7),
            dict(snapshot, rgb_tolerance_l1=True),
            dict(snapshot, rgb_tolerance_l1=1.5),
            dict(snapshot, unknown_property=1),
        ):
            with self.subTest(corrupt=corrupt):
                with self.assertRaises(CuriosityError):
                    ProvisionalObjectMemory.from_snapshot(corrupt)

    def test_single_channel_sensor_jitter_retains_tentative_track(self):
        learner = ProvisionalObjectMemory(rgb_tolerance=1)
        s, d = numeric_frame(0, (10, 10, 10))
        first = learner.observe(s, d, (1.0, 1.0))
        s, d = numeric_frame(1, (11, 10, 10))
        second = learner.observe(s, d, (1.0, 1.0))
        self.assertEqual(first["hypothesis"], second["hypothesis"])
        self.assertEqual(second["status"], "continuous_candidate")
        self.assertEqual(len(learner.tracks), 1)
        self.assertFalse(second["identity_verified"])

    def test_nonmatching_jitter_remains_new_tentative_hypothesis(self):
        learner = ProvisionalObjectMemory(rgb_tolerance=1)
        s, d = numeric_frame(0, (10, 10, 10))
        learner.observe(s, d, (1.0, 1.0))
        s, d = numeric_frame(1, (12, 10, 10))
        other = learner.observe(s, d, (1.0, 1.0))
        self.assertEqual(other["status"], "new_hypothesis")
        self.assertEqual(other["hypothesis"], "h0002")
        self.assertEqual(len(learner.tracks), 2)

    def test_between_two_nearby_colors_abstains_instead_of_merging(self):
        learner = ProvisionalObjectMemory(rgb_tolerance=1, contact_guard=True)
        for tick, color in enumerate(((10, 10, 10), (12, 10, 10))):
            s, d = numeric_frame(tick, color)
            learner.observe(s, d, (1.0, 1.0))
        before = learner.snapshot()
        s, d = numeric_frame(2, (11, 10, 10))
        ambiguity = learner.observe(s, d, (1.0, 1.0))
        self.assertEqual(ambiguity["status"], "ambiguous_identity")
        self.assertEqual(ambiguity["candidates"], ["h0001", "h0002"])
        self.assertIsNone(ambiguity["hypothesis"])
        self.assertFalse(ambiguity["identity_verified"])
        self.assertEqual(before["tracks"], learner.tracks)
        self.assertEqual(before["accepted_sightings"],
                         learner.accepted_sightings)
        self.assertEqual(before["touch_updates"], learner.touch_updates)

    def test_full_track_capacity_rejects_tick_without_mutating_model(self):
        learner = ProvisionalObjectMemory()
        for i in range(MAX_TRACKS):
            s, d = numeric_frame(i, (i, 0, 0))
            result = learner.observe(s, d, (1, 2))
            self.assertEqual(result["status"], "new_hypothesis")
        before = learner.snapshot()
        self.assertEqual(len(before["tracks"]), MAX_TRACKS)
        s, d = numeric_frame(MAX_TRACKS, (MAX_TRACKS, 0, 0))
        with self.assertRaisesRegex(CuriosityError, "capacity"):
            learner.observe(s, d, (1, 2))
        self.assertEqual(learner.snapshot(), before)

    def test_restart_pin_rejects_silent_configuration_changes(self):
        with tempfile.TemporaryDirectory() as root:
            db = Path(root) / "child.sqlite3"
            with SensoryPlay(db, GRAPH, object_tracking=True,
                             contact_guard=True, rgb_tolerance=1) as child:
                for _ in range(35):
                    child.step()
                first = child.checkpoint()
                self.assertEqual(
                    first["object_memory"]["rgb_tolerance_l1"], 1)
                self.assertEqual(child.store.dataset()[
                    "projection"]["items"], [])
            with SensoryPlay(db, GRAPH, object_tracking=True,
                             contact_guard=True, rgb_tolerance=1) as child:
                self.assertEqual(first, child.checkpoint())
                child.step()
            for config in (
                {"object_tracking": True, "contact_guard": True, "rgb_tolerance": 0},
                {"object_tracking": True, "contact_guard": False, "rgb_tolerance": 1},
                {"object_tracking": False},
            ):
                with self.subTest(config=config):
                    with self.assertRaises(CuriosityError):
                        SensoryPlay(db, GRAPH, **config)

    def test_rgb_tolerance_is_not_legal_without_tracking(self):
        with tempfile.TemporaryDirectory() as root:
            db = Path(root) / "not-created.sqlite3"
            for invalid in (-1, 7, True, "1"):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(CuriosityError):
                        SensoryPlay(db, GRAPH, object_tracking=True,
                                    rgb_tolerance=invalid)
            with self.assertRaises(CuriosityError):
                SensoryPlay(db, GRAPH, rgb_tolerance=1)
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
