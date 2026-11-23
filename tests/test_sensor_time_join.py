import copy
from dataclasses import FrozenInstanceError
import math
import unittest

from mojum.local_cell_sense import Proprioception
from mojum.sensor_time_join import (
    MAX_FRAMES, PROPRIO_SOURCE, SCHEMA, SensorJoinError,
    audit_sensor_time_frames,
)


def make_frame(tick, dt_ns=10_000_000, *, pulse=False, light="dim"):
    # Canonical producer's public v1 proprioception representation.
    proprio = Proprioception(tick, f"{tick + 1:064x}", 1., 2., pulse).public()
    return {
        "tick": tick,
        "interoception": {
            "kind": "interoception", "observed_at_ns": tick * dt_ns,
            "heartbeat": {
                "beat_index": tick // 100, "phase_milli": (tick * 10) % 1000,
                "pulse": pulse, "synthetic": True,
            },
        },
        "world": {"kind": "static-room-fixture", "light": light,
                  "observed_at_ns": tick * dt_ns},
        "proprioception": proprio,
    }


class SyntheticSensorJoinTests(unittest.TestCase):
    def test_metadata_and_replay(self):
        window = [make_frame(t) for t in range(20)]
        original = copy.deepcopy(window)
        a = audit_sensor_time_frames(window, dt_ns=10_000_000)
        b = audit_sensor_time_frames(tuple(original), dt_ns=10_000_000)
        self.assertEqual(a, b)
        self.assertEqual(a.schema, SCHEMA)
        self.assertEqual((a.start_tick, a.frame_count, a.first_observed_at_ns,
                          a.last_observed_at_ns), (0, 20, 0, 190_000_000))
        self.assertEqual(len(a.sha256), 64)
        self.assertEqual(window, original)
        self.assertFalse(hasattr(a, "frames"))
        with self.assertRaises(FrozenInstanceError):
            a.frame_count = 100

    def test_window_can_start_after_zero(self):
        a = audit_sensor_time_frames([make_frame(100), make_frame(101)],
                                     dt_ns=10_000_000, start_tick=100)
        self.assertEqual(a.last_observed_at_ns, 1_010_000_000)

    def test_digest_changes_for_admitted_metadata(self):
        a = make_frame(0)
        b = copy.deepcopy(a)
        b["proprioception"]["position_xy"] = [9., 2.]
        x = audit_sensor_time_frames([a], dt_ns=10_000_000)
        y = audit_sensor_time_frames([b], dt_ns=10_000_000)
        self.assertNotEqual(x.sha256, y.sha256)

    def test_empty_oversized_and_wrong_container(self):
        for frames in ([], (), {}, "hi", [make_frame(0)] * (MAX_FRAMES + 1)):
            with self.subTest(type=type(frames)), self.assertRaises(SensorJoinError):
                audit_sensor_time_frames(frames, dt_ns=10_000_000)

    def test_bad_configuration(self):
        for dt in (True, -1, 0, 999, 1_000_000_001, 0.1, "10000", 10**1000):
            with self.subTest(dt=str(dt)[:20]), self.assertRaises(SensorJoinError):
                audit_sensor_time_frames([make_frame(0)], dt_ns=dt)
        for start in (True, -1, 9999, 0.1, 10**1000):
            with self.subTest(start=str(start)[:20]), self.assertRaises(SensorJoinError):
                audit_sensor_time_frames([make_frame(0)], dt_ns=10_000_000,
                                         start_tick=start)

    def test_tick_gap_duplicate_and_wrong_start(self):
        for frames in ([make_frame(0), make_frame(2)],
                       [make_frame(0), make_frame(0)],
                       [make_frame(1)]):
            with self.subTest(frames=frames), self.assertRaises(SensorJoinError):
                audit_sensor_time_frames(frames, dt_ns=10_000_000)

    def test_wrong_interoception_time_and_type(self):
        for value in (True, -1, 1, 10_000_001, "0", 0.0):
            frame = make_frame(0)
            frame["interoception"]["observed_at_ns"] = value
            with self.subTest(value=value), self.assertRaises(SensorJoinError):
                audit_sensor_time_frames([frame], dt_ns=10_000_000)

    def test_wrong_world_time_and_visibility(self):
        for key, value in (("observed_at_ns", 1), ("kind", "PRIVATE"),
                           ("light", "secret"), ("light", 1)):
            f = make_frame(0)
            f["world"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(SensorJoinError):
                audit_sensor_time_frames([f], dt_ns=10_000_000)

    def test_static_light_cannot_change(self):
        with self.assertRaises(SensorJoinError):
            audit_sensor_time_frames([make_frame(0), make_frame(1, light="bright")],
                                     dt_ns=10_000_000)

    def test_pulse_cross_check(self):
        f = make_frame(0)
        f["proprioception"]["heartbeat_pulse"] = True
        with self.assertRaises(SensorJoinError):
            audit_sensor_time_frames([f], dt_ns=10_000_000)

    def test_reject_beat_regression(self):
        frames = [make_frame(100), make_frame(101)]
        frames[0]["interoception"]["heartbeat"]["beat_index"] = 2
        frames[1]["interoception"]["heartbeat"]["beat_index"] = 1
        with self.assertRaises(SensorJoinError):
            audit_sensor_time_frames(frames, dt_ns=10_000_000, start_tick=100)

    def test_reject_bad_heartbeat_shapes(self):
        for key, value in (("phase_milli", True), ("phase_milli", 1000),
                           ("phase_milli", -1), ("pulse", 1),
                           ("synthetic", False), ("beat_index", -1),
                           ("beat_index", 10**1000)):
            f = make_frame(0)
            f["interoception"]["heartbeat"][key] = value
            with self.subTest(key=key), self.assertRaises(SensorJoinError):
                audit_sensor_time_frames([f], dt_ns=10_000_000)

    def test_reject_forged_proprioception_labels(self):
        for key, value in (("schema", "mojum.other/1"),
                           ("source", "PRIVATE"), ("source", False),
                           ("tick", True), ("tick", 1),
                           ("heartbeat_pulse", 0),
                           ("world_revision", "A" * 64),
                           ("world_revision", "g" * 64)):
            f = make_frame(0)
            f["proprioception"][key] = value
            with self.subTest(key=key), self.assertRaises(SensorJoinError):
                audit_sensor_time_frames([f], dt_ns=10_000_000)

    def test_reject_invalid_coordinates(self):
        for value in (True, math.inf, math.nan, 1001, -1, "1", 10**1000):
            f = make_frame(0)
            f["proprioception"]["position_xy"] = [value, 2.]
            with self.subTest(value=str(value)[:20]), self.assertRaises(SensorJoinError):
                audit_sensor_time_frames([f], dt_ns=10_000_000)
        for value in ((), [1], [1, 2, 3], {"x": 1, "y": 2}):
            f = make_frame(0)
            f["proprioception"]["position_xy"] = value
            with self.assertRaises(SensorJoinError):
                audit_sensor_time_frames([f], dt_ns=10_000_000)

    def test_revision_must_change_on_each_tick(self):
        frames = [make_frame(0), make_frame(1)]
        frames[1]["proprioception"]["world_revision"] = frames[0]["proprioception"]["world_revision"]
        with self.assertRaises(SensorJoinError):
            audit_sensor_time_frames(frames, dt_ns=10_000_000)

    def test_reject_extra_fields_nested_and_forged_type(self):
        class Spoof(str):
            pass
        cases = []
        f = make_frame(0); f["secret"] = "private"; cases.append(f)
        f = make_frame(0); f["world"]["secret"] = "private"; cases.append(f)
        f = make_frame(0); f["interoception"]["heartbeat"]["private"] = "secret"; cases.append(f)
        f = make_frame(0); f["proprioception"]["private"] = "secret"; cases.append(f)
        f = make_frame(0); f["proprioception"]["schema"] = Spoof(f["proprioception"]["schema"]); cases.append(f)
        f = make_frame(0); f["world"] = dict(secret="private"); cases.append(f)
        for f in cases:
            with self.assertRaises(SensorJoinError):
                audit_sensor_time_frames([f], dt_ns=10_000_000)

    def test_no_private_data_in_exception_or_witness(self):
        f = make_frame(0)
        f["world"]["private_payload"] = "TOPSECRET_NEVER_OUTPUT"
        with self.assertRaises(SensorJoinError) as e:
            audit_sensor_time_frames([f], dt_ns=10_000_000)
        self.assertNotIn("TOPSECRET_NEVER_OUTPUT", str(e.exception))

    def test_maximum_legal_tick(self):
        witness = audit_sensor_time_frames([make_frame(9998)],
                                           dt_ns=10_000_000, start_tick=9998)
        self.assertEqual(witness.start_tick, 9998)
        next_frame = make_frame(9998)
        next_frame["tick"] = 9999
        next_frame["proprioception"]["tick"] = 9999
        with self.assertRaises(SensorJoinError):
            audit_sensor_time_frames([make_frame(9998), next_frame],
                                     dt_ns=10_000_000, start_tick=9998)


if __name__ == "__main__":
    unittest.main()
