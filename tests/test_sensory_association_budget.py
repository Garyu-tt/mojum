"""The contact association update cap must never produce an unloadable snapshot."""

import unittest

from mojum.sensory_play import (MAX_EVENTS, MODEL_SCHEMA, SENSE_SCHEMA,
                                ContactAssociations, CuriosityError)


def _cue(tick=5, rgb=(1, 2, 3)):
    return {"schema": SENSE_SCHEMA, "tick": tick, "world_revision": "a" * 64,
            "visible": True, "reflectance_rgb": list(rgb), "range_bucket_10cm": 1,
            "source": "synthetic_local_proximity_not_camera"}


def _contact(tick=5, value=0.4):
    return {"contact": True, "instrument": "synthetic_probe_not_material_classifier",
            "source_tick": tick, "measured_deflection": value}


def _model(updates):
    return ContactAssociations.from_snapshot({
        "schema": MODEL_SCHEMA, "updates": updates,
        "table": {"1:2:3": {"count": updates, "mean_deflection": 0.5}},
    })


class AssociationBudgetTests(unittest.TestCase):
    def test_last_admitted_update_stays_loadable(self):
        model = _model(MAX_EVENTS - 1)
        result = model.learn(_cue(), _contact(), model.predict(_cue()))
        self.assertEqual(result["updates"], MAX_EVENTS)
        restored = ContactAssociations.from_snapshot(model.snapshot())
        self.assertEqual(restored.snapshot(), model.snapshot())

    def test_update_past_budget_is_refused_without_mutation(self):
        for rgb in ((1, 2, 3), (9, 9, 9)):  # known cue and new cue
            with self.subTest(rgb=rgb):
                model = _model(MAX_EVENTS)
                before = model.snapshot()
                cue = _cue(rgb=rgb)
                with self.assertRaisesRegex(CuriosityError, "update budget reached"):
                    model.learn(cue, _contact(), model.predict(cue))
                self.assertEqual(model.snapshot(), before)
                ContactAssociations.from_snapshot(model.snapshot())

    def test_no_contact_observation_still_allowed_at_budget(self):
        model = _model(MAX_EVENTS)
        miss = {"contact": False, "instrument": "synthetic_probe_not_material_classifier",
                "source_tick": 5, "measured_deflection": None}
        result = model.learn(_cue(), miss, model.predict(_cue()))
        self.assertFalse(result["learned"])
        self.assertEqual(model.updates, MAX_EVENTS)


if __name__ == "__main__":
    unittest.main()
