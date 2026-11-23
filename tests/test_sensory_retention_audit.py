"""Hermetic tests for the read-only association retention auditor."""
from copy import deepcopy
import math
import unittest

from mojum.sensory_associations_v2 import ContactAssociationsV2
from mojum.sensory_retention_audit import (
    SOURCE_SCHEMA, RetentionAuditError, audit_snapshot,
)
from mojum.sensory_play import SENSE_SCHEMA


def sensor(tick, rgb):
    return {
        "schema": SENSE_SCHEMA,
        "tick": tick,
        "world_revision": "a" * 64,
        "visible": True,
        "reflectance_rgb": list(rgb),
        "range_bucket_10cm": 0,
        "source": "synthetic_local_proximity_not_camera",
    }


def measured(tick):
    return {
        "contact": True,
        "measured_deflection": 0.4,
        "instrument": "synthetic_probe_not_material_classifier",
        "source_tick": tick,
    }


def valid_snapshot():
    return {
        "schema": SOURCE_SCHEMA,
        "table": {"42:90:174": {"count": 3, "mean_deflection": 0.4}},
        "updates": 3,
        "last_seen_at": {"42:90:174": 2},
        "last_contact_tick": 2,
        "evictions": 0,
        "forgotten_observations": 0,
    }


class RetentionAuditTests(unittest.TestCase):
    def test_valid_snapshot_returns_redacted_deterministic_report(self):
        snapshot = valid_snapshot()
        report = audit_snapshot(snapshot)
        self.assertTrue(report["valid"])
        self.assertEqual(report["updates"], 3)
        self.assertEqual(report["retained_observations"], 3)
        self.assertEqual(report["retained_cues"], 1)
        self.assertEqual(report["oldest_retained_age_ticks"], 0)
        self.assertEqual(report, audit_snapshot(deepcopy(snapshot)))
        self.assertNotIn("42:90:174", repr(report))

    def test_retained_plus_forgotten_must_equal_cumulative_updates(self):
        forged = valid_snapshot()
        forged["forgotten_observations"] = 1
        with self.assertRaises(RetentionAuditError):
            audit_snapshot(forged)

    def test_eviction_must_be_possible_and_accounted(self):
        forged = valid_snapshot()
        forged.update(evictions=1, forgotten_observations=0)
        with self.assertRaises(RetentionAuditError):
            audit_snapshot(forged)
        forged.update(evictions=1, forgotten_observations=1, updates=4)
        forged["table"]["42:90:174"]["count"] = 3
        forged["last_contact_tick"] = 3
        forged["last_seen_at"]["42:90:174"] = 3
        with self.assertRaises(RetentionAuditError):
            audit_snapshot(forged)

    def test_valid_eviction_conservation_is_accepted(self):
        table, last_seen = {}, {}
        for tick in range(2, 130):
            cue_key = f"{tick}:0:0"
            table[cue_key] = {"count": 1, "mean_deflection": 0.5}
            last_seen[cue_key] = tick
        snapshot = {
            "schema": SOURCE_SCHEMA, "table": table, "updates": 130,
            "last_seen_at": last_seen, "last_contact_tick": 129,
            "evictions": 2, "forgotten_observations": 2,
        }
        report = audit_snapshot(snapshot)
        self.assertEqual(report["retained_observations"], 128)
        self.assertEqual(report["forgotten_observations"], 2)
        self.assertEqual(report["evictions"], 2)

    def test_rejects_duplicate_or_future_last_seen_ticks(self):
        forged = valid_snapshot()
        forged["table"]["1:2:3"] = {"count": 1, "mean_deflection": 0.2}
        forged["last_seen_at"]["1:2:3"] = 2
        forged["table"]["42:90:174"]["count"] = 2
        with self.assertRaises(RetentionAuditError):
            audit_snapshot(forged)
        forged["last_seen_at"]["1:2:3"] = 3
        with self.assertRaises(RetentionAuditError):
            audit_snapshot(forged)

    def test_rejects_non_native_types_and_non_finite_measurements(self):
        class StrangeInt(int):
            def __eq__(self, other):
                raise RuntimeError("must not execute")
        variants = []
        bad = valid_snapshot(); bad["updates"] = True; variants.append(bad)
        bad = valid_snapshot(); bad["updates"] = StrangeInt(3); variants.append(bad)
        bad = valid_snapshot(); bad["table"]["42:90:174"]["mean_deflection"] = math.nan; variants.append(bad)
        bad = valid_snapshot(); bad["schema"] = SOURCE_SCHEMA + "-legacy"; variants.append(bad)
        for item in variants:
            with self.subTest(item=item):
                with self.assertRaises(RetentionAuditError):
                    audit_snapshot(item)

    def test_rejects_hostile_mapping_keys_before_set_comparison(self):
        class KeyBomb:
            armed = False
            def __hash__(self):
                return hash("schema")
            def __eq__(self, other):
                if self.armed:
                    raise RuntimeError("hostile equality must not execute")
                return False
        forged = valid_snapshot()
        bomb = KeyBomb()
        forged[bomb] = "ignored"
        bomb.armed = True
        with self.assertRaises(RetentionAuditError):
            audit_snapshot(forged)
        forged = valid_snapshot()
        entry = forged["table"]["42:90:174"]
        bomb = KeyBomb()
        entry[bomb] = 1
        bomb.armed = True
        with self.assertRaises(RetentionAuditError):
            audit_snapshot(forged)

    def test_rejects_unbounded_or_malformed_snapshots(self):
        forged = valid_snapshot()
        forged["table"]["999:0:0"] = {"count": 1, "mean_deflection": 0.2}
        forged["last_seen_at"]["999:0:0"] = 1
        with self.assertRaises(RetentionAuditError):
            audit_snapshot(forged)
        with self.assertRaises(RetentionAuditError):
            audit_snapshot({"schema": SOURCE_SCHEMA})

    def test_rejects_oversized_last_seen_map_before_key_comparison(self):
        forged = valid_snapshot()
        forged["last_seen_at"].update({f"{tick}:0:0": tick for tick in range(128)})
        with self.assertRaises(RetentionAuditError):
            audit_snapshot(forged)

    def test_empty_birth_snapshot_is_valid(self):
        snapshot = {
            "schema": SOURCE_SCHEMA, "table": {}, "updates": 0,
            "last_seen_at": {}, "last_contact_tick": -1,
            "evictions": 0, "forgotten_observations": 0,
        }
        report = audit_snapshot(snapshot)
        self.assertEqual(report["updates"], 0)
        self.assertIsNone(report["oldest_retained_age_ticks"])

    def test_actual_model_snapshot_after_4001_updates_is_auditable(self):
        model = ContactAssociationsV2()
        for tick in range(4001):
            frame = sensor(tick, (42, 90, 174))
            model.learn(frame, measured(tick), model.predict(frame))
        report = audit_snapshot(model.snapshot())
        self.assertEqual(report["updates"], 4001)
        self.assertEqual(report["retained_observations"], 4001)
        self.assertEqual(report["forgotten_observations"], 0)
        self.assertEqual(report["last_contact_tick"], 4000)

    def test_actual_model_snapshot_after_two_lru_evictions_is_auditable(self):
        model = ContactAssociationsV2()
        for tick in range(130):
            rgb = (tick, 0, 0)
            frame = sensor(tick, rgb)
            model.learn(frame, measured(tick), model.predict(frame))
        report = audit_snapshot(model.snapshot())
        self.assertEqual(report["updates"], 130)
        self.assertEqual(report["retained_cues"], 128)
        self.assertEqual(report["retained_observations"], 128)
        self.assertEqual(report["forgotten_observations"], 2)
        self.assertEqual(report["evictions"], 2)


if __name__ == "__main__":
    unittest.main()
