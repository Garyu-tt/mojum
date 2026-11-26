"""Hermetic stdlib tests for passive standing-window candidate audit."""
import copy
import unittest

from mojum.standing_window_audit import (
    I64_MAX, MAX_FRAMES, StandingWindowError, audit_standing_window,
)


DT = 20_000_000


def packet(tick=0, time_ns=0):
    return {
        "schema": "mojum.virtual-physics-observation/1",
        "tick": tick,
        "time_ns": time_ns,
        "backend": "mujoco",
        "synthetic": True,
        "physical_contact": False,
        "physical_actuation": False,
        "joints": [
            {"id": "hip.yaw", "position_rad": 0.1, "velocity_rad_s": 0.01},
            {"id": "knee", "position_rad": -0.2, "velocity_rad_s": 0.02},
        ],
        "virtual_contacts": [
            {"geom_a": "left_foot", "geom_b": "floor", "normal_force_n": 40.0},
            {"geom_a": "right_foot", "geom_b": "floor", "normal_force_n": 38.0},
        ],
    }


def window(n=251, start_tick=30, start_time=7_000_000_000):
    return [packet(start_tick + i, start_time + i * DT) for i in range(n)]


def check(frames, **kwargs):
    return audit_standing_window(frames, expected_dt_ns=DT, **kwargs)


class StandingWindowTests(unittest.TestCase):
    def test_exact_five_second_candidate_not_standing_proof(self):
        frames = window()
        receipt = check(frames)
        self.assertTrue(receipt["stationary_support_candidate"])
        self.assertEqual(receipt["duration_ns"], 5_000_000_000)
        self.assertEqual(receipt["observed_min_floor_force_n"], 78.0)
        self.assertEqual(receipt["observed_max_joint_speed_rad_s"], 0.02)
        self.assertEqual(receipt["reasons"], [])
        self.assertFalse(receipt["standing_milestone_achieved"])
        self.assertFalse(receipt["perturbation_recovery_achieved"])
        self.assertFalse(receipt["source_authenticated"])
        self.assertEqual(len(receipt["evidence_sha256"]), 64)

    def test_stable_receipt_and_no_input_mutation(self):
        frames = window(3)
        before = copy.deepcopy(frames)
        first = check(frames)
        self.assertEqual(first, check(frames))
        self.assertEqual(frames, before)
        frames[1]["joints"][0]["position_rad"] += 0.1
        self.assertNotEqual(first["evidence_sha256"], check(frames)["evidence_sha256"])

    def test_short_window_returns_candidate_false(self):
        result = check(window(250))
        self.assertEqual(result["reasons"], ["insufficient_duration"])
        self.assertFalse(result["stationary_support_candidate"])

    def test_missing_floor_support_and_no_implicit_ground(self):
        frames = window(251)
        frames[100]["virtual_contacts"] = []
        result = check(frames)
        self.assertEqual(result["reasons"], ["insufficient_floor_support"])

    def test_unrelated_contact_not_floor(self):
        frames = window(251)
        frames[90]["virtual_contacts"][0]["geom_b"] = "wall"
        frames[90]["virtual_contacts"][1]["geom_b"] = "wall"
        self.assertEqual(check(frames)["reasons"], ["insufficient_floor_support"])

    def test_excessive_joint_motion_and_consistent_failure_order(self):
        frames = window(2)
        frames[1]["joints"][1]["velocity_rad_s"] = 0.101
        result = check(frames)
        self.assertEqual(result["reasons"], [
            "insufficient_duration", "excessive_joint_motion",
        ])
        self.assertFalse(result["stationary_support_candidate"])

    def test_reverse_floor_contact_and_exact_threshold(self):
        frames = window()
        frames[20]["virtual_contacts"][0]["geom_a"] = "floor"
        frames[20]["virtual_contacts"][0]["geom_b"] = "left_foot"
        self.assertTrue(check(frames, min_floor_force_n=78.0)["stationary_support_candidate"])
        self.assertFalse(check(frames, min_floor_force_n=78.001)["stationary_support_candidate"])

    def test_tick_gap_refused(self):
        frames = window(3)
        frames[2]["tick"] += 1
        with self.assertRaisesRegex(StandingWindowError, "clock"):
            check(frames)

    def test_time_gap_refused(self):
        frames = window(3)
        frames[1]["time_ns"] += 1
        with self.assertRaisesRegex(StandingWindowError, "clock"):
            check(frames)

    def test_backend_and_joint_inventory_changes_refused(self):
        for field in ("backend", "joints"):
            with self.subTest(field=field):
                frames = window(3)
                if field == "backend":
                    frames[1][field] = "fixture"
                else:
                    frames[1][field][0]["id"] = "ankle"
                with self.assertRaises(StandingWindowError):
                    check(frames)

    def test_joint_order_does_not_change_inventory(self):
        frames = window()
        frames[1]["joints"].reverse()
        self.assertTrue(check(frames)["stationary_support_candidate"])

    def test_native_container_types_only(self):
        frames = window(2)
        for bad in (tuple(frames), [packet()], [packet()] * (MAX_FRAMES + 1)):
            with self.subTest(size=len(bad)):
                with self.assertRaises(StandingWindowError):
                    check(bad)
        frames = window(2)
        frames[0]["joints"] = tuple(frames[0]["joints"])
        with self.assertRaises(StandingWindowError):
            check(frames)
        frames = window(2)
        frames[0]["joints"][0] = dict_subclass(frames[0]["joints"][0])
        with self.assertRaises(StandingWindowError):
            check(frames)

    def test_budget_checks_before_validation(self):
        frames = window(2)
        frames[0]["joints"] *= 49
        with self.assertRaises(StandingWindowError):
            check(frames)
        frames = window(2)
        frames[0]["virtual_contacts"] *= 9
        with self.assertRaises(StandingWindowError):
            check(frames)
        frames = window(2)
        frames[0]["joints"] = []
        with self.assertRaises(StandingWindowError):
            check(frames)

    def test_illegal_clock_and_threshold_types(self):
        frames = window(2)
        for args in (
            {"expected_dt_ns": True},
            {"expected_dt_ns": 0},
            {"expected_dt_ns": 1_000_000_001},
            {"expected_dt_ns": DT, "required_duration_ns": True},
            {"expected_dt_ns": DT, "required_duration_ns": -1},
            {"expected_dt_ns": DT, "min_floor_force_n": float("nan")},
            {"expected_dt_ns": DT, "min_floor_force_n": True},
            {"expected_dt_ns": DT, "max_joint_speed_rad_s": float("inf")},
            {"expected_dt_ns": DT, "max_joint_speed_rad_s": 10**1000},
        ):
            with self.subTest(args=str(args)[:100]):
                with self.assertRaises(StandingWindowError):
                    audit_standing_window(frames, **args)

    def test_wide_integer_boundary_no_wrap(self):
        frames = [packet(I64_MAX - 1, I64_MAX - DT),
                  packet(I64_MAX, I64_MAX)]
        result = check(frames)
        self.assertEqual(result["end_tick"], I64_MAX)
        frames[1]["time_ns"] += 1
        with self.assertRaises(StandingWindowError):
            check(frames)

    def test_false_hardware_claim_refused(self):
        for field, value in (
            ("physical_contact", True),
            ("physical_actuation", True),
            ("synthetic", False),
        ):
            with self.subTest(field=field):
                frames = window(2)
                frames[1][field] = value
                with self.assertRaises(StandingWindowError):
                    check(frames)

    def test_unsafe_packet_fields_fail_closed(self):
        frames = window(2)
        frames[0]["malicious"] = "private-data"
        with self.assertRaises(StandingWindowError):
            check(frames)
        frames = window(2)
        frames[0]["virtual_contacts"][0]["normal_force_n"] = float("nan")
        with self.assertRaises(StandingWindowError):
            check(frames)

    def test_hostile_keys_never_trigger_equality(self):
        class HostileKey(str):
            def __eq__(self, other):
                raise AssertionError("hostile comparator must never execute")
            __hash__ = str.__hash__

        for where in ("frame", "joint", "contact"):
            with self.subTest(where=where):
                frames = window(2)
                target = (frames[0] if where == "frame" else
                          frames[0]["joints"][0] if where == "joint" else
                          frames[0]["virtual_contacts"][0])
                key = next(iter(target))
                value = target.pop(key)
                target[HostileKey(key)] = value
                with self.assertRaises(StandingWindowError):
                    check(frames)

    def test_no_forged_backend_or_geometry_selectors(self):
        class PretendStr(str):
            def __eq__(self, other):
                return True

        for field in ("backend", "schema"):
            frames = window(2)
            frames[0][field] = PretendStr(frames[0][field])
            with self.assertRaises(StandingWindowError):
                check(frames)
        frames = window(2)
        frames[0]["virtual_contacts"][0]["geom_b"] = PretendStr("floor")
        with self.assertRaises(StandingWindowError):
            check(frames)

    def test_full_frame_budget(self):
        frames = window(MAX_FRAMES)
        result = check(frames)
        self.assertEqual(result["frame_count"], MAX_FRAMES)
        self.assertTrue(result["stationary_support_candidate"])


def dict_subclass(value):
    class AlienDict(dict):
        pass
    return AlienDict(value)


if __name__ == "__main__":
    unittest.main()
