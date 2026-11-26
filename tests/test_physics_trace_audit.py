"""Pure-stdlib regression tests for the isolated passive physics trace audit."""
import copy
import unittest

from mojum.physics_trace_audit import (
    MAX_FRAMES, MAX_WIRE_I64, SCHEMA, audit_physics_trace,
)


def packet(tick=2, time_ns=20_000_000, *, backend="fixture"):
    return {
        "schema": "mojum.virtual-physics-observation/1",
        "tick": tick,
        "time_ns": time_ns,
        "backend": backend,
        "synthetic": True,
        "physical_contact": False,
        "physical_actuation": False,
        "joints": [
            {"id": "hip.yaw", "position_rad": 0.5, "velocity_rad_s": 0.0},
            {"id": "knee", "position_rad": 0.1, "velocity_rad_s": 0.2},
        ],
        "virtual_contacts": [
            {"geom_a": "foot_L", "geom_b": "floor", "normal_force_n": 9.0},
        ],
    }


def trace():
    return [packet(2 + i, 20_000_000 + i * 10_000_000) for i in range(3)]


def audit(frames=None, **kwargs):
    options = {
        "start_tick": 2, "start_time_ns": 20_000_000,
        "step_ns": 10_000_000,
        "data_classification": "PUBLIC_SYNTHETIC", "consent": True,
    }
    options.update(kwargs)
    return audit_physics_trace(trace() if frames is None else frames, **options)


class PhysicsTraceAuditTests(unittest.TestCase):
    def test_deterministic_metadata_only_and_stable_input(self):
        frames = trace()
        original = copy.deepcopy(frames)
        result = audit(frames)
        self.assertEqual(frames, original)
        self.assertEqual(result, audit(copy.deepcopy(frames)))
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual((result["start_tick"], result["end_tick"]), (2, 4))
        self.assertEqual((result["start_time_ns"], result["end_time_ns"]), (20_000_000, 40_000_000))
        self.assertEqual(result["frame_count"], 3)
        self.assertEqual(result["joint_count"], 2)
        self.assertEqual(result["virtual_contact_count"], 3)
        self.assertEqual(len(result["trace_sha256"]), 64)
        self.assertNotIn("foot_L", str(result))
        self.assertNotIn("hip.yaw", str(result))
        self.assertNotIn("position_rad", str(result))

    def test_data_changes_invalidate_digest(self):
        frames = trace()
        before = audit(frames)
        frames[1]["joints"][0]["position_rad"] = -1.25
        after = audit(frames)
        self.assertNotEqual(before["trace_sha256"], after["trace_sha256"])
        self.assertEqual(before["frame_count"], after["frame_count"])

    def test_joint_order_is_not_identity_drift(self):
        frames = trace()
        frames[1]["joints"].reverse()
        self.assertEqual(audit(frames)["joint_count"], 2)

    def test_rejects_temporal_discontinuity_and_mismatched_anchor(self):
        for key, value in (("tick", 10), ("tick", 2), ("time_ns", 30_000_001),
                           ("time_ns", 20_000_000)):
            frames = trace()
            frames[1][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                audit(frames)
        with self.assertRaises(ValueError):
            audit(start_tick=1)
        with self.assertRaises(ValueError):
            audit(step_ns=1)

    def test_rejects_backend_or_joint_inventory_drift(self):
        frames = trace()
        frames[2]["backend"] = "mujoco"
        with self.assertRaisesRegex(ValueError, "backend"):
            audit(frames)
        frames = trace()
        frames[1]["joints"][1]["id"] = "ankle"
        with self.assertRaisesRegex(ValueError, "inventory"):
            audit(frames)

    def test_rejects_physical_and_bad_measurement_claims(self):
        for key, value in (("synthetic", False), ("physical_contact", True),
                           ("physical_actuation", True)):
            frames = trace()
            frames[2][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                audit(frames)
        frames = trace()
        frames[1]["joints"][0]["position_rad"] = float("nan")
        with self.assertRaises(ValueError):
            audit(frames)

    def test_consent_and_public_synthetic_are_exact(self):
        for value in (False, 1, "true", None):
            with self.subTest(consent=value), self.assertRaises(ValueError):
                audit(consent=value)
        for value in (None, "PRIVATE", "public_synthetic", True, 7):
            with self.subTest(classification=value), self.assertRaises(ValueError):
                audit(data_classification=value)

    def test_rejects_hostile_container_types_and_frame_limits(self):
        for frames in ([], (), {}, "bad", [None], [packet()] * (MAX_FRAMES + 1)):
            with self.subTest(frames_type=type(frames).__name__), self.assertRaises(ValueError):
                audit(frames)
        class HostileList(list):
            def __iter__(self):
                raise AssertionError("must reject subclass before iteration")
        with self.assertRaises(ValueError):
            audit(HostileList(trace()))
        frames = [packet(i, i) for i in range(MAX_FRAMES)]
        self.assertEqual(audit(frames, start_tick=0, start_time_ns=0, step_ns=1)["frame_count"], MAX_FRAMES)

    def test_rejects_false_clock_types_and_signed_overflow(self):
        for field, value in (("start_tick", True), ("start_tick", -1),
                             ("start_tick", 1.5), ("start_time_ns", False),
                             ("start_time_ns", -1), ("step_ns", True),
                             ("step_ns", 0), ("step_ns", 1_000_000_001)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                audit(**{field: value})
        near_end = [packet(MAX_WIRE_I64 - 1, MAX_WIRE_I64 - 10),
                    packet(MAX_WIRE_I64, MAX_WIRE_I64)]
        result = audit(near_end, start_tick=MAX_WIRE_I64 - 1,
                       start_time_ns=MAX_WIRE_I64 - 10, step_ns=10)
        self.assertEqual(result["end_tick"], MAX_WIRE_I64)
        with self.assertRaises(ValueError):
            audit(near_end, start_tick=MAX_WIRE_I64, start_time_ns=MAX_WIRE_I64 - 10, step_ns=10)
        with self.assertRaises(ValueError):
            audit(near_end, start_tick=MAX_WIRE_I64 - 1, start_time_ns=MAX_WIRE_I64 - 9, step_ns=10)

    def test_total_joint_and_contact_budgets(self):
        frames = [packet(i, i) for i in range(3)]
        for frame in frames:
            frame["virtual_contacts"] = [
                {"geom_a": "foot_L", "geom_b": "floor", "normal_force_n": 1.0}
                for _ in range(3000)
            ]
        with self.assertRaisesRegex(ValueError, "budget"):
            audit(frames, start_tick=0, start_time_ns=0, step_ns=1)
        frames = [packet(i, i) for i in range(17)]
        for frame in frames:
            frame["joints"] = [
                {"id": f"joint{i}", "position_rad": 0, "velocity_rad_s": 0}
                for i in range(256)
            ]
            frame["virtual_contacts"] = []
        with self.assertRaisesRegex(ValueError, "budget"):
            audit(frames, start_tick=0, start_time_ns=0, step_ns=1)


if __name__ == "__main__":
    unittest.main()
