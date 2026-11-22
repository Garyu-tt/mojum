import copy
import unittest

from mojum.physics_observation import validate_virtual_physics_observation


def sample():
    return {
        "schema": "mojum.virtual-physics-observation/1",
        "tick": 2,
        "time_ns": 20_000_000,
        "backend": "mujoco",
        "synthetic": True,
        "physical_contact": False,
        "physical_actuation": False,
        "joints": [{
            "id": "hip.yaw", "position_rad": 0.5, "velocity_rad_s": 0.0,
        }],
        "virtual_contacts": [{
            "geom_a": "foot_L", "geom_b": "floor", "normal_force_n": 9.0,
        }],
    }


class VirtualPhysicsObservationTests(unittest.TestCase):
    def test_valid_and_detached(self):
        original = sample()
        result = validate_virtual_physics_observation(original)
        original["joints"][0]["position_rad"] = 99
        self.assertEqual(result["joints"][0]["position_rad"], 0.5)
        self.assertFalse(result["physical_contact"])
        self.assertEqual(result["backend"], "mujoco")

    def test_rejects_untrusted_hardware_claims(self):
        for key, value in (("synthetic", False), ("physical_contact", True),
                           ("physical_actuation", True)):
            with self.subTest(key=key):
                p = sample()
                p[key] = value
                with self.assertRaises(ValueError):
                    validate_virtual_physics_observation(p)

    def test_rejects_nonfinite_and_bool_values(self):
        for reading in (float("nan"), float("inf"), True, 10**1000):
            p = sample()
            p["joints"][0]["velocity_rad_s"] = reading
            with self.subTest(reading=str(reading)[:15]):
                with self.assertRaises(ValueError):
                    validate_virtual_physics_observation(p)

    def test_rejects_duplicate_joint_and_bad_geometry(self):
        p = sample()
        p["joints"].append(copy.deepcopy(p["joints"][0]))
        with self.assertRaises(ValueError):
            validate_virtual_physics_observation(p)
        p = sample()
        p["virtual_contacts"][0]["geom_a"] = "../dev"
        with self.assertRaises(ValueError):
            validate_virtual_physics_observation(p)

    def test_rejects_unknown_fields_and_backend(self):
        p = sample()
        p["unsafe"] = "x"
        with self.assertRaises(ValueError):
            validate_virtual_physics_observation(p)
        p = sample()
        p["backend"] = "real-robot"
        with self.assertRaises(ValueError):
            validate_virtual_physics_observation(p)

    def test_rejects_bad_timestamps(self):
        for bad in (-1, True, 0.5, "2"):
            p = sample()
            p["time_ns"] = bad
            with self.assertRaises(ValueError):
                validate_virtual_physics_observation(p)

    def test_rejects_out_of_range_wire_clock_fields(self):
        for field in ("tick", "time_ns"):
            with self.subTest(field=field):
                packet = sample()
                packet[field] = 2**63 - 1
                self.assertEqual(validate_virtual_physics_observation(packet)[field], 2**63 - 1)
                packet[field] = 2**63
                with self.assertRaises(ValueError):
                    validate_virtual_physics_observation(packet)

    def test_bounded_lists(self):
        p = sample()
        p["joints"] = [copy.deepcopy(p["joints"][0]) for _ in range(257)]
        with self.assertRaises(ValueError):
            validate_virtual_physics_observation(p)
        p = sample()
        p["virtual_contacts"] *= 4097
        with self.assertRaises(ValueError):
            validate_virtual_physics_observation(p)

    def test_rejects_malformed_input(self):
        for bad in (None, [], "{}", {"schema": "mojum.virtual-physics-observation/1"}):
            with self.assertRaises(ValueError):
                validate_virtual_physics_observation(bad)


if __name__ == "__main__":
    unittest.main()
