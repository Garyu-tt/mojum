"""Tests for isolated, non-metabolic optional food/water sensory experiences."""

import copy
import unittest

from mojum.optional_consumption import (
    EVENT_SCHEMA, FRAME_SCHEMA, LEDGER_SCHEMA,
    OptionalConsumptionError, OptionalConsumptionLedger, validate_event,
)


def experience(event_id="e1", tick=0, kind="water_sip"):
    return {
        "schema": EVENT_SCHEMA, "event_id": event_id,
        "organism_id": "synthetic-dante", "tick": tick, "kind": kind,
        "stimulus_id": "water" if kind == "water_sip" else "apple",
        "flavor": "neutral" if kind == "water_sip" else "sweet",
        "temperature": "cool", "texture": "liquid" if kind == "water_sip" else "crunchy",
        "opt_in": True, "source": "scripted_fixture",
    }


class OptionalConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.ledger = OptionalConsumptionLedger("synthetic-dante")

    def test_empty_ledger_never_deprives_even_at_large_tick(self):
        initial = self.ledger.revision
        for tick in (0, 1, 1000, 2**63 - 1):
            frame = self.ledger.frame(tick)
            self.assertEqual(frame["schema"], FRAME_SCHEMA)
            self.assertEqual(frame["events_this_tick"], [])
            self.assertIs(frame["food_required_for_survival"], False)
            self.assertIs(frame["water_required_for_survival"], False)
            self.assertIsNone(frame["deprivation_effect"])
        self.assertEqual(self.ledger.revision, initial)

    def test_water_and_food_are_optional_positive_experiences(self):
        self.ledger.append(experience(), self.ledger.revision)
        self.ledger.append(experience("e2", 1, "food_taste"), self.ledger.revision)
        self.assertEqual(self.ledger.frame(0)["events_this_tick"][0]["kind"], "water_sip")
        self.assertEqual(self.ledger.frame(1)["events_this_tick"][0]["kind"], "food_taste")
        self.assertIsNone(self.ledger.frame(10000)["deprivation_effect"])

    def test_multiple_experiences_per_tick_in_append_order(self):
        self.ledger.append(experience(), self.ledger.revision)
        self.ledger.append(experience("e2", 0, "food_taste"), self.ledger.revision)
        self.assertEqual([e["event_id"] for e in self.ledger.frame(0)["events_this_tick"]], ["e1", "e2"])

    def test_snapshot_and_replay_are_deterministic(self):
        for e in (experience(), experience("e2", 8, "food_taste")):
            self.ledger.append(e, self.ledger.revision)
        snapshot = self.ledger.snapshot()
        self.assertEqual(snapshot["schema"], LEDGER_SCHEMA)
        replay = OptionalConsumptionLedger.from_snapshot(snapshot)
        self.assertEqual(replay.revision, self.ledger.revision)
        for tick in (0, 1, 8, 900):
            self.assertEqual(replay.frame(tick), self.ledger.frame(tick))

    def test_snapshot_and_frame_are_defensive_copies(self):
        e = experience()
        self.ledger.append(e, self.ledger.revision)
        rev = self.ledger.revision
        e["flavor"] = "bitter"
        snapshot = self.ledger.snapshot()
        snapshot["events"][0]["flavor"] = "sour"
        self.ledger.frame(0)["events_this_tick"][0]["texture"] = "other"
        self.assertEqual(self.ledger.frame(0)["events_this_tick"][0]["flavor"], "neutral")
        self.assertEqual(self.ledger.revision, rev)

    def test_opt_in_must_be_literal_true(self):
        for bad in (False, 1, 0, "true", None, []):
            with self.subTest(value=bad):
                e = experience()
                e["opt_in"] = bad
                with self.assertRaises(OptionalConsumptionError):
                    self.ledger.append(e, self.ledger.revision)
        self.assertEqual(self.ledger.snapshot()["events"], [])

    def test_rejects_unknown_and_missing_fields_including_hunger(self):
        for mutation in (lambda e: e.update({"hunger": 1}),
                         lambda e: e.update({"calories": 20}),
                         lambda e: e.pop("flavor")):
            e = experience()
            mutation(e)
            with self.assertRaises(OptionalConsumptionError):
                self.ledger.append(e, self.ledger.revision)

    def test_rejects_foreign_identity_and_invalid_ascii_identifier(self):
        for bad in ("foreign", "\ud800", "", "a" * 65, "a/b", "こんにちは"):
            e = experience()
            e["organism_id"] = bad
            with self.assertRaises(OptionalConsumptionError):
                self.ledger.append(e, self.ledger.revision)

    def test_wrong_stimulus_and_texture_for_water_rejected(self):
        for field, value in (("stimulus_id", "poison"), ("texture", "crunchy")):
            e = experience()
            e[field] = value
            with self.assertRaises(OptionalConsumptionError):
                self.ledger.append(e, self.ledger.revision)

    def test_unknown_kind_source_or_sensory_tags_rejected(self):
        for field, bad in (("kind", "feed"), ("source", "network://untrusted"),
                           ("flavor", "unknown"), ("temperature", 1), ("texture", True)):
            e = experience(kind="food_taste")
            e[field] = bad
            with self.assertRaises(OptionalConsumptionError):
                self.ledger.append(e, self.ledger.revision)

    def test_typed_tick_and_range(self):
        for bad in (True, 1.0, "2", -1, 2**63, None):
            with self.subTest(value=bad):
                e = experience(tick=bad)
                with self.assertRaises(OptionalConsumptionError):
                    self.ledger.append(e, self.ledger.revision)
                with self.assertRaises(OptionalConsumptionError):
                    self.ledger.frame(bad)

    def test_rejects_stale_missing_revision(self):
        old = self.ledger.revision
        self.ledger.append(experience(), old)
        for stale in (old, None, True):
            with self.assertRaisesRegex(OptionalConsumptionError, "CAS"):
                self.ledger.append(experience("e2", 1), stale)
        self.assertEqual(len(self.ledger.snapshot()["events"]), 1)

    def test_duplicate_event_and_non_monotonic_tick_rejected(self):
        self.ledger.append(experience(tick=5), self.ledger.revision)
        for e in (experience(tick=5), experience("e2", tick=4)):
            with self.assertRaises(OptionalConsumptionError):
                self.ledger.append(e, self.ledger.revision)
        self.assertEqual(len(self.ledger.snapshot()["events"]), 1)

    def test_event_limit_rejects_without_mutating(self):
        self.ledger.MAX_EVENTS = 1
        self.ledger.append(experience(), self.ledger.revision)
        before = self.ledger.revision
        with self.assertRaises(OptionalConsumptionError):
            self.ledger.append(experience("e2", 1), before)
        self.assertEqual(self.ledger.revision, before)

    def test_snapshot_rejects_shape_tampering_and_extra_fields(self):
        self.ledger.append(experience(), self.ledger.revision)
        original = self.ledger.snapshot()
        variants = []
        changed_flavor = copy.deepcopy(original)
        changed_flavor["events"][0]["flavor"] = "bitter"
        variants.append(changed_flavor)
        removed_id = copy.deepcopy(original)
        del removed_id["events"][0]["event_id"]
        variants.append(removed_id)
        changed_organism = copy.deepcopy(original)
        changed_organism["organism_id"] = "foreign"
        variants.append(changed_organism)
        duplicate = copy.deepcopy(original)
        duplicate["events"].append(copy.deepcopy(duplicate["events"][0]))
        variants.append(duplicate)
        extra = copy.deepcopy(original)
        extra["hydration"] = 100
        variants.append(extra)
        for damaged in variants:
            with self.subTest(damaged=damaged):
                with self.assertRaises(OptionalConsumptionError):
                    OptionalConsumptionLedger.from_snapshot(damaged)
        self.assertEqual(self.ledger.snapshot(), original)

    def test_validate_event_copies_and_rejects_non_object(self):
        e = experience()
        copied = validate_event(e, "synthetic-dante")
        self.assertEqual(copied, e)
        self.assertIsNot(copied, e)
        for invalid in (None, [], "event", 4):
            with self.assertRaises(OptionalConsumptionError):
                validate_event(invalid, "synthetic-dante")

    def test_empty_snapshot_restores_without_maintenance_clock(self):
        replay = OptionalConsumptionLedger.from_snapshot(self.ledger.snapshot())
        self.assertEqual(replay.frame(2**63 - 1), self.ledger.frame(2**63 - 1))


if __name__ == "__main__":
    unittest.main()
