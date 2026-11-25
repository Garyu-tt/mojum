from __future__ import annotations

import copy
import math
import unittest
from dataclasses import FrozenInstanceError

from mojum.cloth_contact_telemetry import (
    MAX_FORCE_N,
    MAX_SAMPLES,
    PUBLIC_SYNTHETIC,
    SCHEMA,
    ContactTelemetryError,
    reduce_contacts,
)


REV = "rig-r01"


def sample(**overrides):
    value = {
        "tick": 1,
        "observed_at_ns": 100,
        "rig_revision": REV,
        "contact_id": "contact-1",
        "surface": "body",
        "normal_force_n": 2.0,
        "tension_n": 3.0,
        "compression_n": 4.0,
    }
    value.update(overrides)
    return value


def reduce(items, **kwargs):
    options = {"rig_revision": REV, "provenance": PUBLIC_SYNTHETIC}
    options.update(kwargs)
    return reduce_contacts(items, **options)


class ContactTelemetryHappyPathTests(unittest.TestCase):
    def test_groups_by_surface_and_uses_deterministic_surface_order(self):
        report = reduce([
            sample(contact_id="b1", surface="body", normal_force_n=2,
                   tension_n=1, compression_n=0),
            sample(tick=2, observed_at_ns=200, contact_id="h1", surface="hand",
                   normal_force_n=5, tension_n=2, compression_n=1),
            sample(tick=2, observed_at_ns=200, contact_id="w1", surface="world",
                   normal_force_n=7, tension_n=0, compression_n=3),
            sample(tick=3, observed_at_ns=300, contact_id="b2", surface="body",
                   normal_force_n=4, tension_n=2, compression_n=1),
        ])
        result = report.to_dict()
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["sample_count"], 4)
        self.assertEqual((result["first_tick"], result["last_tick"]), (1, 3))
        self.assertEqual(
            [item["surface"] for item in result["surfaces"]],
            ["body", "hand", "world"],
        )
        body = result["surfaces"][0]
        self.assertEqual(body["contacts"], 2)
        self.assertEqual(body["normal_force_sum_n"], 6.0)
        self.assertEqual(body["normal_force_max_n"], 4.0)
        self.assertEqual(body["tension_sum_n"], 3.0)
        self.assertEqual(body["compression_sum_n"], 1.0)

    def test_empty_input_is_a_valid_empty_observation(self):
        result = reduce([]).to_dict()
        self.assertEqual(result["sample_count"], 0)
        self.assertEqual(result["surfaces"], [])
        self.assertIsNone(result["first_tick"])
        self.assertIsNone(result["last_observed_at_ns"])

    def test_deterministic_repeated_reduction(self):
        values = [sample(), sample(tick=2, observed_at_ns=200, contact_id="second")]
        first = reduce(values).to_dict()
        second = reduce(copy.deepcopy(values)).to_dict()
        self.assertEqual(first, second)

    def test_order_changes_digest_even_when_aggregate_totals_match(self):
        a = sample(contact_id="a", normal_force_n=1)
        b = sample(contact_id="b", normal_force_n=2)
        ordered = reduce([a, b]).input_sha256
        reversed_order = reduce([b, a]).input_sha256
        self.assertNotEqual(ordered, reversed_order)

    def test_multiple_surfaces_and_same_tick_time_are_allowed(self):
        report = reduce([
            sample(contact_id="body", surface="body"),
            sample(contact_id="hand", surface="hand"),
        ])
        self.assertEqual(report.sample_count, 2)

    def test_same_contact_id_on_later_tick_is_allowed(self):
        report = reduce([
            sample(contact_id="stable"),
            sample(tick=2, observed_at_ns=200, contact_id="stable"),
        ])
        self.assertEqual(report.sample_count, 2)

    def test_zero_force_values_are_valid(self):
        report = reduce([sample(normal_force_n=0, tension_n=0, compression_n=0)])
        self.assertEqual(report.to_dict()["surfaces"][0]["normal_force_max_n"], 0.0)

    def test_force_upper_bound_is_inclusive(self):
        report = reduce([sample(normal_force_n=MAX_FORCE_N)])
        self.assertEqual(report.to_dict()["surfaces"][0]["normal_force_max_n"], MAX_FORCE_N)

    def test_input_is_not_mutated_and_result_is_detached(self):
        original = sample()
        before = copy.deepcopy(original)
        result = reduce([original])
        self.assertEqual(original, before)
        original["normal_force_n"] = 999
        self.assertEqual(result.to_dict()["surfaces"][0]["normal_force_sum_n"], 2.0)

    def test_to_dict_returns_fresh_nested_values(self):
        report = reduce([sample()])
        first = report.to_dict()
        first["surfaces"][0]["contacts"] = 999
        second = report.to_dict()
        self.assertEqual(second["surfaces"][0]["contacts"], 1)

    def test_report_is_immutable(self):
        report = reduce([sample()])
        with self.assertRaises(FrozenInstanceError):
            report.sample_count = 10
        with self.assertRaises(FrozenInstanceError):
            report.surfaces[0].contacts = 10
        self.assertIsInstance(report.surfaces, tuple)

    def test_hashes_are_sha256_hex(self):
        report = reduce([sample()]).to_dict()
        for field in ("input_sha256", "report_sha256"):
            self.assertRegex(report[field], r"^[0-9a-f]{64}$")


class ContactTelemetryAdmissionTests(unittest.TestCase):
    def test_provenance_must_be_explicit_and_exact(self):
        for value in (None, "", "PRIVATE", "public_synthetic", PUBLIC_SYNTHETIC + " "):
            with self.subTest(value=value):
                with self.assertRaises(ContactTelemetryError):
                    reduce([sample()], provenance=value)

    def test_rig_revision_is_required_and_validated(self):
        for value in (None, "", 1, True, "../rig", "x" * 129):
            with self.subTest(value=value):
                with self.assertRaises(ContactTelemetryError):
                    reduce([sample()], rig_revision=value)

    def test_sample_revision_must_match_admitted_revision(self):
        with self.assertRaisesRegex(ContactTelemetryError, "does not match"):
            reduce([sample(rig_revision="other-rig")])

    def test_samples_must_be_bounded_sequence(self):
        for values in ((sample(),), iter([sample()]), "not samples", {"one": sample()}):
            with self.subTest(type=type(values).__name__):
                with self.assertRaises(ContactTelemetryError):
                    reduce(values)

    def test_sample_budget_is_enforced_before_iteration(self):
        values = [sample(tick=i, observed_at_ns=i, contact_id=f"c{i}")
                  for i in range(MAX_SAMPLES + 1)]
        with self.assertRaisesRegex(ContactTelemetryError, "exceeds"):
            reduce(values)

    def test_sample_requires_exact_plain_dict_shape(self):
        with self.assertRaises(ContactTelemetryError):
            reduce([{"tick": 1}])
        extra = sample(extra="not allowed")
        with self.assertRaises(ContactTelemetryError):
            reduce([extra])

        class DictSubclass(dict):
            pass

        with self.assertRaises(ContactTelemetryError):
            reduce([DictSubclass(sample())])

    def test_sample_rejects_string_subclass_keys(self):
        class TextSubclass(str):
            pass

        original = sample()
        hostile = {
            (TextSubclass(key) if key == "tick" else key): value
            for key, value in original.items()
        }
        with self.assertRaises(ContactTelemetryError):
            reduce([hostile])

    def test_tick_rejects_bool_float_negative_and_overflow(self):
        for value in (True, False, 1.0, -1, 2**63):
            with self.subTest(value=value):
                with self.assertRaises(ContactTelemetryError):
                    reduce([sample(tick=value)])

    def test_observed_clock_rejects_bool_float_negative_and_overflow(self):
        for value in (True, 3.5, -1, 2**63):
            with self.subTest(value=value):
                with self.assertRaises(ContactTelemetryError):
                    reduce([sample(observed_at_ns=value)])

    def test_tick_must_not_move_backwards(self):
        with self.assertRaisesRegex(ContactTelemetryError, "tick moves backwards"):
            reduce([
                sample(tick=5, observed_at_ns=100, contact_id="a"),
                sample(tick=4, observed_at_ns=101, contact_id="b"),
            ])

    def test_observed_clock_must_not_move_backwards(self):
        with self.assertRaisesRegex(ContactTelemetryError, "moves backwards"):
            reduce([
                sample(tick=1, observed_at_ns=101, contact_id="a"),
                sample(tick=2, observed_at_ns=100, contact_id="b"),
            ])

    def test_duplicate_contact_id_within_tick_is_rejected(self):
        with self.assertRaisesRegex(ContactTelemetryError, "duplicate"):
            reduce([
                sample(contact_id="same"),
                sample(contact_id="same", surface="hand"),
            ])

    def test_surface_is_allowlisted(self):
        for value in ("finger", "BODY", "", None, 1, True):
            with self.subTest(value=value):
                with self.assertRaises(ContactTelemetryError):
                    reduce([sample(surface=value)])

    def test_contact_id_is_bounded_and_plain_text(self):
        for value in ("", "bad id", "../x", "x" * 97, "zero\nline", None, 3):
            with self.subTest(value=value):
                with self.assertRaises(ContactTelemetryError):
                    reduce([sample(contact_id=value)])

    def test_force_rejects_negative_nonfinite_overflow_and_non_numeric(self):
        for value in (-0.1, math.nan, math.inf, -math.inf, MAX_FORCE_N + 1,
                      "2.0", None, True, 10**1000):
            with self.subTest(value=repr(value)[:24]):
                with self.assertRaises(ContactTelemetryError):
                    reduce([sample(normal_force_n=value)])

    def test_each_force_channel_is_validated(self):
        for field in ("normal_force_n", "tension_n", "compression_n"):
            with self.subTest(field=field):
                with self.assertRaises(ContactTelemetryError):
                    reduce([sample(**{field: -1})])


if __name__ == "__main__":
    unittest.main()
