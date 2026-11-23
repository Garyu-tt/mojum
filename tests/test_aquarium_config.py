from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError

from mojum.aquarium_config import (
    MAX_PATH_CHARS,
    MAX_PROFILE_BYTES,
    SCHEMA,
    AquariumConfigError,
    load_profile_text,
    validate_profile,
)


def profile(**overrides):
    value = {"schema": SCHEMA, "db_path": "./dante_PRIVATE.sqlite3"}
    value.update(overrides)
    return value


class AquariumConfigTests(unittest.TestCase):
    def test_minimal_profile_uses_stable_documented_defaults(self):
        result = validate_profile(profile())
        self.assertEqual(result.ticks, 120)
        self.assertEqual(result.dt_ns, 10_000_000)
        self.assertEqual(result.neural_mode, "shadow")
        self.assertEqual(result.motor_mode, "scripted")
        self.assertEqual(result.brain_mode, "placeholder")
        self.assertEqual(result.sensory_mode, "none")
        self.assertEqual(result.temporal_mode, "none")
        self.assertEqual(result.pulse_sum, "auto")
        self.assertFalse(result.read_only)
        self.assertIsNone(result.room_file)
        self.assertEqual(len(result.sha256), 64)

    def test_json_text_and_native_mapping_have_same_digest(self):
        source = profile(
            ticks=9, motor_mode="developmental_fixture",
            sensory_mode="toy_probe", temporal_mode="range_guided",
        )
        mapping_result = validate_profile(source)
        text_result = load_profile_text(json.dumps(source))
        self.assertEqual(mapping_result, text_result)
        self.assertEqual(mapping_result.sha256, text_result.sha256)

    def test_room_file_and_database_paths_are_inert_intent(self):
        result = validate_profile(profile(room_file="../room.json"))
        self.assertEqual(result.db_path, "./dante_PRIVATE.sqlite3")
        self.assertEqual(result.room_file, "../room.json")
        options = result.runtime_options()
        self.assertNotIn("db_path", options)
        self.assertEqual(options["room_file"], "../room.json")

    def test_result_is_frozen_and_exports_are_detached(self):
        result = validate_profile(profile())
        with self.assertRaises(FrozenInstanceError):
            result.ticks = 2
        exported = result.to_dict()
        exported["ticks"] = 999
        self.assertEqual(result.ticks, 120)
        options = result.runtime_options()
        options["ticks"] = 999
        self.assertEqual(result.ticks, 120)

    def test_digest_is_independent_of_input_key_order(self):
        first = profile(ticks=25, read_only=True)
        second = dict(reversed(list(first.items())))
        self.assertEqual(validate_profile(first).sha256, validate_profile(second).sha256)

    def test_unknown_or_missing_fields_fail_closed(self):
        for value in (
            {"schema": SCHEMA},
            profile(unexpected="ignored"),
            profile(schema="mojum.aquarium.launch-profile/999"),
        ):
            with self.subTest(value=value), self.assertRaises(AquariumConfigError):
                validate_profile(value)

    def test_native_integer_admission_rejects_bool_and_subclass(self):
        class IntSubclass(int):
            pass

        for value in (True, False, 1.0, "3", IntSubclass(3), 0, 1_001):
            with self.subTest(value=value), self.assertRaises(AquariumConfigError):
                validate_profile(profile(ticks=value))
        for value in (True, 999, 1.0, 0, 1_000_000_001):
            with self.subTest(dt_ns=value), self.assertRaises(AquariumConfigError):
                validate_profile(profile(dt_ns=value))

    def test_boolean_must_be_exact_boolean(self):
        for value in (0, 1, "false", None):
            with self.subTest(value=value), self.assertRaises(AquariumConfigError):
                validate_profile(profile(read_only=value))

    def test_unsupported_modes_and_cross_field_conflicts_fail(self):
        bad_profiles = (
            profile(neural_mode="maybe"),
            profile(motor_mode="unknown"),
            profile(brain_mode="developmental_controller"),
            profile(sensory_mode="toy_probe"),
            profile(motor_mode="local_cell_v4", sensory_mode="toy_probe"),
            profile(motor_mode="scripted", sensory_mode="toy_probe_v2"),
            profile(temporal_mode="range_guided"),
            profile(neural_mode="disabled", pulse_sum="ordered"),
        )
        for value in bad_profiles:
            with self.subTest(value=value), self.assertRaises(AquariumConfigError):
                validate_profile(value)

    def test_supported_compositions_are_admitted(self):
        result = validate_profile(profile(
            motor_mode="developmental_fixture",
            brain_mode="developmental_controller",
            sensory_mode="toy_probe",
            temporal_mode="range_guided",
            pulse_sum="compensated",
            ticks=50,
        ))
        self.assertEqual(result.motor_mode, "developmental_fixture")
        v4 = validate_profile(profile(motor_mode="local_cell_v4", sensory_mode="toy_probe_v2"))
        self.assertEqual(v4.sensory_mode, "toy_probe_v2")

    def test_archive_mode_requires_zero_ticks_and_other_modes_reject_zero(self):
        archive = validate_profile(profile(
            motor_mode="local_cell_v2_a_archive",
            ticks=0,
        ))
        self.assertEqual(archive.ticks, 0)
        with self.assertRaises(AquariumConfigError):
            validate_profile(profile(motor_mode="local_cell_v2_a_archive", ticks=1))
        with self.assertRaises(AquariumConfigError):
            validate_profile(profile(ticks=0))

    def test_fixed_geometry_local_cell_modes_reject_room_file(self):
        fixed_modes = (
            "local_cell_v2b",
            "local_cell_v3",
            "local_cell_v3_escape",
            "local_cell_v4",
            "local_cell_v2",
            "local_cell_v2_a_archive",
        )
        for mode in fixed_modes:
            with self.subTest(mode=mode), self.assertRaises(AquariumConfigError):
                validate_profile(profile(motor_mode=mode, room_file="./room.json"))
        # Selectors that accept a caller-provided room retain that option.
        self.assertEqual(
            validate_profile(profile(motor_mode="developmental_fixture", room_file="./room.json")).room_file,
            "./room.json",
        )

    def test_json_duplicate_keys_and_nonfinite_values_are_rejected(self):
        duplicate = (
            '{"schema":"' + SCHEMA + '","db_path":"a","ticks":1,"ticks":2}'
        )
        with self.assertRaises(AquariumConfigError):
            load_profile_text(duplicate)
        nonfinite = '{"schema":"' + SCHEMA + '","db_path":"a","ticks":NaN}'
        with self.assertRaises(AquariumConfigError):
            load_profile_text(nonfinite)
        infinity = '{"schema":"' + SCHEMA + '","db_path":"a","ticks":Infinity}'
        with self.assertRaises(AquariumConfigError):
            load_profile_text(infinity)

    def test_json_root_must_be_object_and_nested_values_are_rejected(self):
        for value in ('[]', 'null', '1', json.dumps(profile(ticks={"value": 2}))):
            with self.subTest(value=value), self.assertRaises(AquariumConfigError):
                load_profile_text(value)

    def test_json_byte_limit_and_recursion_failure_are_typed(self):
        oversized = " " * (MAX_PROFILE_BYTES + 1)
        with self.assertRaises(AquariumConfigError):
            load_profile_text(oversized)
        deeply_nested = '{"schema":"' + SCHEMA + '","db_path":"a","ticks":' + ("[" * 2000) + "0" + ("]" * 2000) + "}"
        with self.assertRaises(AquariumConfigError):
            load_profile_text(deeply_nested)

    def test_only_exact_dict_is_accepted(self):
        class DictSubclass(dict):
            pass

        with self.assertRaises(AquariumConfigError):
            validate_profile(DictSubclass(profile()))

    def test_path_values_are_bounded_and_reject_invisible_controls(self):
        bad_values = ("", " name.sqlite", "name.sqlite ", "a\x00b", "a\nb", "x" * (MAX_PATH_CHARS + 1), 7)
        for value in bad_values:
            with self.subTest(value=repr(value)), self.assertRaises(AquariumConfigError):
                validate_profile(profile(db_path=value))
        for value in ("", "bad\x1bpath", "x" * (MAX_PATH_CHARS + 1), 7):
            with self.subTest(room_file=repr(value)), self.assertRaises(AquariumConfigError):
                validate_profile(profile(room_file=value))

    def test_schema_subclass_and_non_native_key_are_rejected(self):
        class StrangeString(str):
            def __eq__(self, other):
                raise RuntimeError("hostile equality")

        hostile_schema = profile()
        hostile_schema["schema"] = StrangeString(SCHEMA)
        with self.assertRaises(AquariumConfigError):
            validate_profile(hostile_schema)

        class StrangeKey(str):
            pass

        hostile_key = profile()
        hostile_key[StrangeKey("ticks")] = 10
        with self.assertRaises(AquariumConfigError):
            validate_profile(hostile_key)

    def test_mapping_is_not_mutated(self):
        original = profile(ticks=8, room_file="./room.json")
        before = dict(original)
        validate_profile(original)
        self.assertEqual(original, before)


if __name__ == "__main__":
    unittest.main()
