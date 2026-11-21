import json
import unittest

from mojum.contact_associations_v2 import ContactAssociationsV2, WINDOW
from mojum.sensory_play import ContactAssociations, CuriosityError, MAX_CUES, SENSE_SCHEMA
from mojum.sensory_associations_v2 import (
    SCHEMA as CANONICAL_V2_SCHEMA,
    ContactAssociationsV2 as CanonicalContactAssociationsV2,
)


def sensor(tick, rgb):
    return {"schema": SENSE_SCHEMA, "tick": tick, "world_revision": "0" * 64,
            "visible": True, "reflectance_rgb": list(rgb), "range_bucket_10cm": 3,
            "source": "synthetic_local_proximity_not_camera"}


def feed(m, tick, rgb, value):
    s = sensor(tick, rgb)
    return m.learn(s, {"contact": True, "instrument": "synthetic_probe_not_material_classifier",
                       "source_tick": tick, "measured_deflection": value}, m.predict(s))


class V2Test(unittest.TestCase):
    def test_past_v1_limit_and_roundtrip(self):
        m = ContactAssociationsV2()
        for i in range(4001):
            feed(m, i, (1, 2, 3), 0.5)
        self.assertEqual(m.table["1:2:3"]["count"], WINDOW)
        r = ContactAssociationsV2.from_snapshot(json.loads(json.dumps(m.snapshot())))
        self.assertEqual(r.snapshot(), m.snapshot())

    def test_decay_tracks_change(self):
        m = ContactAssociationsV2()
        for i in range(500):
            feed(m, i, (9, 9, 9), 0.0)
        for i in range(500, 700):
            feed(m, i, (9, 9, 9), 1.0)
        self.assertGreater(m.table["9:9:9"]["mean_deflection"], 0.9)

    def test_eviction_accounting_and_determinism(self):
        def run():
            m = ContactAssociationsV2()
            for i in range(MAX_CUES + 20):
                feed(m, i, (i % 256, i // 256, 0), 0.25)
            return m
        a, b = run(), run()
        self.assertEqual(a.snapshot(), b.snapshot())
        self.assertEqual(len(a.table), MAX_CUES)
        self.assertEqual(a.evicted_cues, 20)
        self.assertNotIn("0:0:0", a.table)
        self.assertEqual(ContactAssociationsV2.from_snapshot(a.snapshot()).snapshot(), a.snapshot())

    def test_windowed_wire_identity_is_distinct_from_canonical_v2(self):
        experimental = ContactAssociationsV2().snapshot()
        canonical = CanonicalContactAssociationsV2().snapshot()
        self.assertNotEqual(experimental["schema"], CANONICAL_V2_SCHEMA)
        with self.assertRaises(CuriosityError):
            ContactAssociationsV2.from_snapshot(canonical)
        with self.assertRaises(CuriosityError):
            CanonicalContactAssociationsV2.from_snapshot(experimental)

    def test_no_implicit_conversion(self):
        v1 = ContactAssociations()
        with self.assertRaises(CuriosityError):
            ContactAssociationsV2.from_snapshot(v1.snapshot())
        with self.assertRaises(CuriosityError):
            ContactAssociations.from_snapshot(ContactAssociationsV2().snapshot())

    def test_duplicate_last_update_rejected(self):
        m = ContactAssociationsV2()
        feed(m, 0, (1, 1, 1), 0.5)
        feed(m, 1, (2, 2, 2), 0.5)
        snapshot = m.snapshot()
        snapshot["table"]["2:2:2"]["last_update"] = snapshot["table"]["1:1:1"]["last_update"]
        with self.assertRaisesRegex(CuriosityError, "duplicate association update"):
            ContactAssociationsV2.from_snapshot(snapshot)

    def test_impossible_eviction_and_update_history_rejected(self):
        m = ContactAssociationsV2()
        feed(m, 0, (1, 1, 1), 0.5)
        feed(m, 1, (2, 2, 2), 0.5)
        feed(m, 2, (1, 1, 1), 0.5)
        latest_missing = m.snapshot()
        # The counters still sum to 3 and timestamps remain distinct,
        # but neither retained cue claims the latest update 3.
        latest_missing["table"]["1:1:1"]["last_update"] = 1
        with self.assertRaises(CuriosityError):
            ContactAssociationsV2.from_snapshot(latest_missing)
        premature = m.snapshot()
        del premature["table"]["1:1:1"]
        premature["evicted_cues"] = 1
        premature["evicted_observations"] = 2
        with self.assertRaisesRegex(CuriosityError, "eviction history"):
            ContactAssociationsV2.from_snapshot(premature)
        erased = m.snapshot()
        erased["table"] = {}
        erased["evicted_cues"] = 2
        erased["evicted_observations"] = 3
        with self.assertRaises(CuriosityError):
            ContactAssociationsV2.from_snapshot(erased)

    def test_feedback_extra_fields_fail_closed_without_partial_learning(self):
        m = ContactAssociationsV2()
        cue = sensor(0, (1, 2, 3))
        before = m.snapshot()
        measurement = {
            "contact": True,
            "instrument": "synthetic_probe_not_material_classifier",
            "source_tick": 0,
            "measured_deflection": 0.5,
            "untrusted_extra": "must not be silently accepted",
        }
        with self.assertRaises(CuriosityError):
            m.learn(cue, measurement, m.predict(cue))
        self.assertEqual(m.snapshot(), before)

    def test_tampered_accounting_rejected(self):
        m = ContactAssociationsV2()
        feed(m, 0, (1, 1, 1), 0.5)
        s = m.snapshot()
        s["evicted_observations"] = 5
        with self.assertRaises(CuriosityError):
            ContactAssociationsV2.from_snapshot(s)


if __name__ == "__main__":
    unittest.main()
