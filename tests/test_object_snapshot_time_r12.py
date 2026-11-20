"""Regression: synthetic object memory evidence can accrue at most once per tick.

An independent review of PR #34 showed that field-valid snapshots could
claim 3,999 experiences on the first tick. Journal replay already checks
full event history; direct snapshot import must also fail closed.
"""

import copy
import unittest

from mojum.object_memory import ProvisionalObjectMemory
from mojum.sensory_play import CuriosityError
from tests.test_object_noise_r11 import numeric_frame


class TemporalObjectSnapshotTests(unittest.TestCase):
    def test_independent_qa_one_tick_3999_evidence_is_rejected(self):
        forged = {
            "schema": "mojum.object-memory.provisional/1",
            "tracks": {
                "h0001": {
                    "cue": "10:20:30", "estimate": [1.0, 1.0],
                    "first_tick": 0, "last_tick": 0,
                    "sightings": 3999, "contacts": 3999,
                    "mean_deflection": 0.5,
                },
            },
            "next_track": 2, "last_tick": 0,
            "accepted_sightings": 3999, "touch_updates": 3999,
        }
        preserved = copy.deepcopy(forged)
        with self.assertRaises(CuriosityError):
            ProvisionalObjectMemory.from_snapshot(forged)
        self.assertEqual(forged, preserved)

    def test_per_track_window_cannot_exceed_elapsed_observation_ticks(self):
        m = ProvisionalObjectMemory()
        for tick in range(3):
            sensor, direction = numeric_frame(tick, (10, 20, 30))
            m.observe(sensor, direction, [1, 1])
        clean = m.snapshot()
        self.assertEqual(clean["tracks"]["h0001"]["sightings"], 3)
        forged = copy.deepcopy(clean)
        forged["tracks"]["h0001"]["first_tick"] = 2
        with self.assertRaises(CuriosityError):
            ProvisionalObjectMemory.from_snapshot(forged)
        self.assertEqual(ProvisionalObjectMemory.from_snapshot(clean).snapshot(), clean)

    def test_total_evidence_cannot_exceed_observation_window(self):
        m = ProvisionalObjectMemory()
        sensor, direction = numeric_frame(0, (10, 20, 30))
        m.observe(sensor, direction, [1, 1])
        forged = m.snapshot()
        forged["accepted_sightings"] = 2
        forged["tracks"]["h0001"]["sightings"] = 2
        with self.assertRaises(CuriosityError):
            ProvisionalObjectMemory.from_snapshot(forged)

    def test_more_tracks_than_accepted_sightings_rejected(self):
        m = ProvisionalObjectMemory()
        sensor, direction = numeric_frame(0, (10, 20, 30))
        m.observe(sensor, direction, [1, 1])
        forged = m.snapshot()
        forged["tracks"]["h0002"] = copy.deepcopy(forged["tracks"]["h0001"])
        forged["next_track"] = 3
        with self.assertRaises(CuriosityError):
            ProvisionalObjectMemory.from_snapshot(forged)

    def test_zero_tick_empty_legacy_and_opt_in_snapshots_remain_compatible(self):
        for mode in ({}, {"contact_guard": True, "rgb_tolerance": 1}):
            learner = ProvisionalObjectMemory(**mode)
            snapshot = learner.snapshot()
            self.assertEqual(snapshot["accepted_sightings"], 0)
            self.assertEqual(snapshot["last_tick"], -1)
            self.assertEqual(
                ProvisionalObjectMemory.from_snapshot(snapshot).snapshot(),
                snapshot,
            )

    def test_valid_dropout_and_ambiguity_preserve_temporal_constraints(self):
        learner = ProvisionalObjectMemory(rgb_tolerance=1)
        sequence = [
            (0, (10, 20, 30), True),
            (1, None, False),
            (2, (10, 20, 30), True),
        ]
        for tick, color, visible in sequence:
            sensor, direction = numeric_frame(tick, color or (10, 20, 30))
            if not visible:
                sensor["visible"] = False
                sensor["reflectance_rgb"] = None
                sensor["range_bucket_10cm"] = None
                direction["octant"] = None
            learner.observe(sensor, direction, [1, 1])
        snapshot = learner.snapshot()
        self.assertEqual(snapshot["last_tick"], 2)
        self.assertEqual(snapshot["accepted_sightings"], 2)
        self.assertEqual(
            ProvisionalObjectMemory.from_snapshot(snapshot).snapshot(),
            snapshot,
        )


if __name__ == "__main__":
    unittest.main()
