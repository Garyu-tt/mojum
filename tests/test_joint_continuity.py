import math
import unittest
from mojum.joint_continuity import audit_joint_continuity


def s(t, p, r="rig-r1"):
    return {"tick": t, "revision": r, "positions": p}


class HostileString(str):
    def __new__(cls, value):
        instance = super().__new__(cls, value)
        instance.explode = False
        return instance

    def __hash__(self):
        if self.explode:
            raise RuntimeError("hostile hash must not run")
        return super().__hash__()


class JointContinuityTests(unittest.TestCase):
    def test_valid_and_deterministic(self):
        rows = [s(10, {"hip": 0.0, "knee": 0.1}), s(11, {"hip": 0.2, "knee": 0.2})]
        a = audit_joint_continuity(rows, {"hip": 0.25, "knee": 0.2}, expected_revision="rig-r1")
        b = audit_joint_continuity(rows, {"hip": 0.25, "knee": 0.2}, expected_revision="rig-r1")
        self.assertTrue(a["ok"])
        self.assertEqual(a["digest_sha256"], b["digest_sha256"])

    def test_threshold_equality(self):
        self.assertTrue(audit_joint_continuity([s(0, {"j": 0}), s(1, {"j": .5})], {"j": .5})["ok"])

    def test_exceedance_first_tick(self):
        r = audit_joint_continuity([s(0, {"j": 0}), s(1, {"j": .4}), s(2, {"j": 1})], {"j": .5})
        self.assertFalse(r["ok"])
        self.assertEqual(r["violations"][0]["first_offending_tick"], 2)

    def test_wrap_is_explicit(self):
        rows = [s(0, {"yaw": math.pi - .05}), s(1, {"yaw": -math.pi + .05})]
        self.assertFalse(audit_joint_continuity(rows, {"yaw": .2})["ok"])
        self.assertTrue(audit_joint_continuity(rows, {"yaw": .2}, revolute=("yaw",))["ok"])

    def test_revolute_tiny_displacement_exceeds_limit(self):
        for position in (-1e-16, 1e-16):
            with self.subTest(position=position):
                rows = [s(0, {"yaw": 0.0}), s(1, {"yaw": position})]
                result = audit_joint_continuity(rows, {"yaw": 1e-17}, revolute=("yaw",))
                self.assertFalse(result["ok"])
                self.assertEqual(result["max_steps_rad"]["yaw"], abs(position))
                self.assertEqual(result["violations"][0]["first_offending_tick"], 1)

    def test_revolute_threshold_equality(self):
        for position in (-0.2, -0.1, 0.1, 0.2):
            with self.subTest(position=position):
                rows = [s(0, {"yaw": 0.0}), s(1, {"yaw": position})]
                result = audit_joint_continuity(rows, {"yaw": abs(position)}, revolute=("yaw",))
                self.assertTrue(result["ok"])
                self.assertEqual(result["max_steps_rad"]["yaw"], abs(position))
                self.assertEqual(result["violations"], [])

    def test_revolute_half_turn(self):
        for position in (-math.pi, math.pi):
            with self.subTest(position=position):
                rows = [s(0, {"yaw": 0.0}), s(1, {"yaw": position})]
                result = audit_joint_continuity(rows, {"yaw": math.pi}, revolute=("yaw",))
                self.assertTrue(result["ok"])
                self.assertEqual(result["max_steps_rad"]["yaw"], math.pi)

    def test_revolute_multiple_turns(self):
        for turns in (-2, -1, 1, 2):
            for residual in (-0.25, 0.0, 0.25):
                with self.subTest(turns=turns, residual=residual):
                    position = turns * math.tau + residual
                    rows = [s(0, {"yaw": 0.0}), s(1, {"yaw": position})]
                    result = audit_joint_continuity(rows, {"yaw": 0.25}, revolute=("yaw",))
                    self.assertTrue(result["ok"])
                    self.assertEqual(result["max_steps_rad"]["yaw"], abs(residual))

    def test_tick_gap_and_bool_tick(self):
        for rows in ([s(0, {"j": 0}), s(2, {"j": 0})], [s(True, {"j": 0})]):
            with self.assertRaises(ValueError):
                audit_joint_continuity(rows, {"j": 1})

    def test_revision_and_joint_set_changes(self):
        for rows in ([s(0, {"j": 0}), s(1, {"j": 0}, "r2")], [s(0, {"j": 0}), s(1, {"k": 0})]):
            with self.assertRaises(ValueError):
                audit_joint_continuity(rows, {"j": 1})

    def test_bool_and_nonfinite_positions(self):
        for value in (True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                audit_joint_continuity([s(0, {"j": value})], {"j": 1})

    def test_revolute_huge_finite_positions_fail_closed(self):
        rows = [s(0, {"yaw": 1e308}), s(1, {"yaw": -1e308})]
        with self.assertRaises(ValueError):
            audit_joint_continuity(rows, {"yaw": 0.2}, revolute=("yaw",))

    def test_hostile_sample_key_rejected_before_hashing(self):
        row = s(0, {"j": 0})
        key = HostileString("tick")
        row[key] = row.pop("tick")
        key.explode = True
        with self.assertRaises(ValueError):
            audit_joint_continuity([row], {"j": 1})

    def test_hostile_position_key_rejected_before_hashing(self):
        positions = {"j": 0}
        key = HostileString("j")
        positions[key] = positions.pop("j")
        key.explode = True
        with self.assertRaises(ValueError):
            audit_joint_continuity([s(0, positions)], {"j": 1})

    def test_oversized_sample_and_position_mappings_rejected_before_iteration(self):
        oversized_sample = s(0, {"j": 0})
        oversized_sample.update({f"extra-{i}": i for i in range(10_000)})
        with self.assertRaisesRegex(ValueError, "invalid sample keys"):
            audit_joint_continuity([oversized_sample], {"j": 1})

        oversized_positions = {"j": 0}
        oversized_positions.update({f"extra-{i}": i for i in range(10_000)})
        with self.assertRaisesRegex(ValueError, "invalid positions keys"):
            audit_joint_continuity([s(0, oversized_positions)], {"j": 1})

    def test_revolute_collection_is_bounded(self):
        oversized = tuple(f"j{i}" for i in range(513))
        with self.assertRaisesRegex(ValueError, "invalid revolute set"):
            audit_joint_continuity([s(0, {"j": 0})], {"j": 1}, revolute=oversized)

    def test_limits_and_revolute_validation(self):
        for limits in ({"j": 0}, {"j": True}, {"j": float("nan")}, {"j": 7}):
            with self.assertRaises(ValueError):
                audit_joint_continuity([s(0, {"j": 0})], limits)
        for rev in (("other",), ("j", "j")):
            with self.assertRaises(ValueError):
                audit_joint_continuity([s(0, {"j": 0})], {"j": 1}, revolute=rev)

    def test_oversized_logical_tick_fails_closed(self):
        with self.assertRaises(ValueError):
            audit_joint_continuity([s(1 << 63, {"j": 0})], {"j": 1})

    def test_bounds_and_schema(self):
        with self.assertRaises(ValueError):
            audit_joint_continuity([s(0, {"j": 0}), s(1, {"j": 0})], {"j": 1}, max_samples=1)
        with self.assertRaises(ValueError):
            audit_joint_continuity([s(0, {"j": 0, "k": 0})], {"j": 1, "k": 1}, max_joints=1)
        with self.assertRaises(ValueError):
            audit_joint_continuity([{"tick": 0, "revision": "r", "positions": {"j": 0}, "extra": 1}], {"j": 1})
        with self.assertRaises(ValueError):
            audit_joint_continuity([], {"j": 1})


if __name__ == "__main__":
    unittest.main()
