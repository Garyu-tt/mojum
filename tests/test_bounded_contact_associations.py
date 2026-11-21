import unittest

from mojum.sensory_play import (BoundedContactAssociations, ContactAssociations,
                                CuriosityError, CUE_WINDOW_V2, MAX_EVENTS,
                                MODEL_SCHEMA, MODEL_SCHEMA_V2, SENSE_SCHEMA)


def _sensor(tick):
    return {"schema": SENSE_SCHEMA, "tick": tick, "world_revision": "a" * 64,
            "visible": True, "reflectance_rgb": [1, 2, 3], "range_bucket_10cm": 2,
            "source": "synthetic_local_proximity_not_camera"}


def _step(m, tick, value):
    s = _sensor(tick)
    return m.learn(s, {"contact": True, "instrument": "synthetic_probe_not_material_classifier",
                       "source_tick": tick, "measured_deflection": value}, m.predict(s))


class BoundedTest(unittest.TestCase):
    def test_runs_past_v1_ceiling_and_replays(self):
        m = BoundedContactAssociations()
        for i in range(MAX_EVENTS + 50):
            _step(m, i, (i % 2) * 1.0)
        e = m.table["1:2:3"]
        self.assertEqual(e["count"], CUE_WINDOW_V2)
        self.assertEqual(e["count"] + e["forgotten"], MAX_EVENTS + 50)
        r = BoundedContactAssociations.from_snapshot(m.snapshot())
        self.assertEqual(r.snapshot(), m.snapshot())

    def test_versions_are_not_interchangeable(self):
        m = BoundedContactAssociations()
        _step(m, 0, 0.5)
        with self.assertRaises(CuriosityError):
            ContactAssociations.from_snapshot(m.snapshot())
        v1 = ContactAssociations()
        self.assertEqual(v1.snapshot()["schema"], MODEL_SCHEMA)
        with self.assertRaises(CuriosityError):
            BoundedContactAssociations.from_snapshot(v1.snapshot())
        self.assertEqual(m.snapshot()["schema"], MODEL_SCHEMA_V2)

    def test_rejects_inconsistent_snapshot(self):
        m = BoundedContactAssociations()
        _step(m, 0, 0.5)
        s = m.snapshot()
        s["table"]["1:2:3"]["forgotten"] = 1
        with self.assertRaises(CuriosityError):
            BoundedContactAssociations.from_snapshot(s)


if __name__ == "__main__":
    unittest.main()
