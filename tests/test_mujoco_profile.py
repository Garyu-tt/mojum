"""Hermetic tests for the offline MuJoCo CPU profile contract."""
import json
import math
import unittest
from unittest.mock import patch

from mojum.mujoco_profile import (
    MAX_CPU_THREADS, MAX_JSON_BYTES, MAX_SIMULATED_NS, MAX_STEPS,
    MAX_TIMESTEP_NS, MIN_TIMESTEP_NS, SCHEMA, CpuProfile, ProfileError,
    loads_profile, validate_profile,
)


def valid_profile(**overrides):
    value = {
        "schema": SCHEMA,
        "enabled": True,
        "timestep_ns": 2_000_000,
        "steps": 1000,
        "warmup_steps": 50,
        "cpu_threads": 2,
        "integrator": "implicitfast",
        "solver": "newton",
    }
    value.update(overrides)
    return value


class CpuProfileTests(unittest.TestCase):
    def test_valid_profile_is_immutable_detached_and_repeatable(self):
        source = valid_profile()
        first = validate_profile(source)
        second = validate_profile(source)
        self.assertIsInstance(first, CpuProfile)
        self.assertEqual(first, second)
        self.assertEqual(first.digest, second.digest)
        source["steps"] = 1
        self.assertEqual(first.steps, 1000)
        exported = first.to_mapping()
        exported["steps"] = 7
        self.assertEqual(first.steps, 1000)
        with self.assertRaises((AttributeError, TypeError)):
            first.steps = 9
        with self.assertRaises(AttributeError):
            first.__dict__

    def test_public_constructor_cannot_bypass_profile_validation(self):
        with self.assertRaisesRegex(ProfileError, "timestep_ns"):
            CpuProfile(
                enabled=True, timestep_ns=0, steps=1000, warmup_steps=50,
                cpu_threads=2, integrator="implicitfast", solver="newton",
                digest="0" * 64,
            )
        valid = validate_profile(valid_profile())
        with self.assertRaisesRegex(ProfileError, "digest"):
            CpuProfile(
                enabled=valid.enabled, timestep_ns=valid.timestep_ns,
                steps=valid.steps, warmup_steps=valid.warmup_steps,
                cpu_threads=valid.cpu_threads, integrator=valid.integrator,
                solver=valid.solver, digest="0" * 64,
            )

    def test_canonical_digest_ignores_input_key_order(self):
        source = valid_profile()
        reverse = dict(reversed(list(source.items())))
        self.assertEqual(validate_profile(source).digest,
                         validate_profile(reverse).digest)

    def test_disabled_profile_is_valid_but_stays_disabled(self):
        profile = validate_profile(valid_profile(enabled=False))
        self.assertFalse(profile.enabled)

    def test_required_fields_are_exact(self):
        for field in valid_profile():
            with self.subTest(field=field):
                missing = valid_profile()
                del missing[field]
                with self.assertRaises(ProfileError):
                    validate_profile(missing)
        extra = valid_profile()
        extra["surprise"] = 1
        with self.assertRaises(ProfileError):
            validate_profile(extra)

    def test_exact_native_types_reject_bool_and_float_integer_aliases(self):
        for field in ("timestep_ns", "steps", "warmup_steps", "cpu_threads"):
            for bad in (True, False, 2.0, "2", None):
                with self.subTest(field=field, bad=bad):
                    with self.assertRaises(ProfileError):
                        validate_profile(valid_profile(**{field: bad}))
        for bad in (0, 1, "true", None):
            with self.subTest(enabled=bad), self.assertRaises(ProfileError):
                validate_profile(valid_profile(enabled=bad))

    def test_timestep_bounds(self):
        for value in (MIN_TIMESTEP_NS - 1, MAX_TIMESTEP_NS + 1):
            with self.subTest(value=value), self.assertRaises(ProfileError):
                validate_profile(valid_profile(timestep_ns=value))
        self.assertEqual(validate_profile(
            valid_profile(timestep_ns=MIN_TIMESTEP_NS)).timestep_ns,
            MIN_TIMESTEP_NS)
        self.assertEqual(validate_profile(
            valid_profile(timestep_ns=MAX_TIMESTEP_NS, steps=1,
                          warmup_steps=0)).timestep_ns,
            MAX_TIMESTEP_NS)

    def test_step_and_thread_bounds(self):
        for field, value in (("steps", 0), ("steps", MAX_STEPS + 1),
                             ("warmup_steps", -1), ("warmup_steps", 1_000_001),
                             ("cpu_threads", 0), ("cpu_threads", MAX_CPU_THREADS + 1)):
            with self.subTest(field=field, value=value), self.assertRaises(ProfileError):
                validate_profile(valid_profile(**{field: value}))

    def test_warmup_cannot_exceed_steps(self):
        with self.assertRaisesRegex(ProfileError, "warmup_steps"):
            validate_profile(valid_profile(steps=10, warmup_steps=11))

    def test_total_simulated_duration_is_bounded(self):
        with self.assertRaisesRegex(ProfileError, "duration"):
            validate_profile(valid_profile(timestep_ns=MAX_TIMESTEP_NS,
                                           steps=MAX_SIMULATED_NS // MAX_TIMESTEP_NS + 1,
                                           warmup_steps=0))
        self.assertTrue(validate_profile(valid_profile(
            timestep_ns=MAX_TIMESTEP_NS,
            steps=MAX_SIMULATED_NS // MAX_TIMESTEP_NS,
            warmup_steps=0)))

    def test_allowlisted_solver_and_integrator_choices_are_independent(self):
        for integrator in ("euler", "implicit", "implicitfast"):
            for solver in ("newton", "cg"):
                with self.subTest(integrator=integrator, solver=solver):
                    profile = valid_profile(integrator=integrator, solver=solver)
                    admitted = validate_profile(profile)
                    self.assertEqual(admitted.integrator, integrator)
                    self.assertEqual(admitted.solver, solver)

    def test_rejects_unknown_solver_integrator_and_non_native_strings(self):
        for field, value in (("solver", "pgs"), ("integrator", "rk4"),
                             ("solver", True), ("integrator", 1)):
            with self.subTest(field=field, value=value), self.assertRaises(ProfileError):
                validate_profile(valid_profile(**{field: value}))

    def test_native_dict_only_not_mapping_subclasses(self):
        class DictSubclass(dict):
            pass
        with self.assertRaises(ProfileError):
            validate_profile(DictSubclass(valid_profile()))

    def test_oversized_direct_mapping_rejected_before_key_snapshot(self):
        value = valid_profile()
        value.update({f"untrusted_{index}": index for index in range(50_000)})
        with patch(
            "mojum.mujoco_profile.frozenset",
            side_effect=AssertionError("key snapshot must not be allocated"),
            create=True,
        ):
            with self.assertRaisesRegex(ProfileError, "missing or unknown fields"):
                validate_profile(value)

    def test_oversized_solver_string_rejected_before_allowlist_hash(self):
        class NoContains:
            def __iter__(self):
                return iter(("newton", "cg"))

            def __contains__(self, value):
                raise AssertionError("allowlist hash must not run")

        with patch("mojum.mujoco_profile._SOLVERS", NoContains()):
            with self.assertRaisesRegex(ProfileError, "unsupported solver"):
                validate_profile(valid_profile(solver="x" * 1_000_000))

    def test_non_string_keys_fail_closed(self):
        value = valid_profile()
        value[1] = "unexpected"
        with self.assertRaises(ProfileError):
            validate_profile(value)

    def test_loads_profile_from_utf8_json_and_bytes(self):
        encoded = json.dumps(valid_profile(), separators=(",", ":"))
        self.assertEqual(loads_profile(encoded), loads_profile(encoded.encode("utf-8")))

    def test_duplicate_json_keys_rejected(self):
        raw = '{"schema":"mojum.mujoco.cpu-profile/1","enabled":true,"enabled":false}'
        with self.assertRaisesRegex(ProfileError, "duplicate"):
            loads_profile(raw)

    def test_json_constants_and_non_finite_values_rejected(self):
        raw = json.dumps(valid_profile(), separators=(",", ":")).replace('"steps":1000', '"steps":NaN')
        with self.assertRaises(ProfileError):
            loads_profile(raw)
        value = valid_profile(timestep_ns=math.inf)
        with self.assertRaises(ProfileError):
            validate_profile(value)

    def test_invalid_json_type_utf8_and_oversize_rejected(self):
        for value in (bytearray(b"{}"), 1, None):
            with self.subTest(value=type(value)), self.assertRaises(ProfileError):
                loads_profile(value)
        with self.assertRaises(ProfileError):
            loads_profile(b"\xff")
        with self.assertRaises(ProfileError):
            loads_profile(" " * (MAX_JSON_BYTES + 1))

    def test_json_depth_limit(self):
        raw = '{"x":' * 12 + "0" + "}" * 12
        with self.assertRaisesRegex(ProfileError, "depth"):
            loads_profile(raw)

    def test_json_schema_validation_after_parse(self):
        wrong = json.dumps(valid_profile(schema="future/9"))
        with self.assertRaises(ProfileError):
            loads_profile(wrong)
        with self.assertRaises(ProfileError):
            loads_profile("[]")
        with self.assertRaises(ProfileError):
            loads_profile("{broken")

    def test_string_payloads_are_byte_bounded_even_with_multibyte_utf8(self):
        raw = '{"x":"' + ("я" * (MAX_JSON_BYTES // 2 + 1)) + '"}'
        with self.assertRaises(ProfileError):
            loads_profile(raw)


if __name__ == "__main__":
    unittest.main()
