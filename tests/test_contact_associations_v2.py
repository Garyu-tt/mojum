import json
import unittest

from mojum.contact_associations_v2 import ContactAssociationsV2, WINDOW
from mojum.sensory_play import ContactAssociations, CuriosityError, MAX_CUES, SENSE_SCHEMA


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

    def test_no_implicit_conversion(self):
        v1 = ContactAssociations()
        with self.assertRaises(CuriosityError):
            ContactAssociationsV2.from_snapshot(v1.snapshot())
        with self.assertRaises(CuriosityError):
            ContactAssociations.from_snapshot(ContactAssociationsV2().snapshot())

    def test_tampered_accounting_rejected(self):
        m = ContactAssociationsV2()
        feed(m, 0, (1, 1, 1), 0.5)
        s = m.snapshot()
        s["evicted_observations"] = 5
        with self.assertRaises(CuriosityError):
            ContactAssociationsV2.from_snapshot(s)


if __name__ == "__main__":
    unittest.main()
