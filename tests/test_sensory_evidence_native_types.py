"""Independent QA: untrusted sensory input must not override Python equality."""
import copy
import unittest

from mojum.sensory_play import (
    ContactAssociations, CuriosityError, MODEL_SCHEMA, SENSE_SCHEMA,
)


def cue():
    return {
        "schema": SENSE_SCHEMA,
        "tick": 0,
        "world_revision": "0" * 64,
        "visible": True,
        "reflectance_rgb": [220, 70, 30],
        "range_bucket_10cm": 2,
        "source": "synthetic_local_proximity_not_camera",
    }


def contact():
    return {
        "contact": True,
        "measured_deflection": 0.5,
        "instrument": "synthetic_probe_not_material_classifier",
        "source_tick": 0,
    }


class EqualitySpoof:
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False


class ForgedPrediction(dict):
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False


class SensoryEvidenceNativeTypesTests(unittest.TestCase):
    def test_canonical_measurement_and_snapshot_unchanged(self):
        m = ContactAssociations()
        observation = cue()
        prediction = m.predict(observation)
        self.assertEqual(prediction["observations"], 0)
        learned = m.learn(observation, contact(), prediction)
        self.assertTrue(learned["learned"])
        self.assertEqual(m.updates, 1)
        self.assertEqual(
            ContactAssociations.from_snapshot(m.snapshot()).snapshot(),
            m.snapshot(),
        )

    def test_equality_spoofed_sensor_schema_and_source_rejected(self):
        for key in ("schema", "source"):
            observation = cue()
            observation[key] = EqualitySpoof()
            with self.subTest(key=key), self.assertRaises(CuriosityError):
                ContactAssociations().predict(observation)

    def test_equality_spoofed_model_schema_rejected(self):
        snapshot = {"schema": EqualitySpoof(), "table": {}, "updates": 0}
        with self.assertRaises(CuriosityError):
            ContactAssociations.from_snapshot(snapshot)
        self.assertEqual(MODEL_SCHEMA, ContactAssociations().snapshot()["schema"])

    def test_forged_prediction_equality_cannot_relabel_learned_cue(self):
        m = ContactAssociations()
        baseline = copy.deepcopy(m.snapshot())
        wrong_cue = ForgedPrediction({
            "cue": "1:1:1", "predicted_deflection": None,
            "observations": 0, "model_updates": 0,
        })
        with self.assertRaises(CuriosityError):
            m.learn(cue(), contact(), wrong_cue)
        self.assertEqual(m.snapshot(), baseline)

    def test_prediction_poisoned_scalar_cannot_bypass_comparison(self):
        m = ContactAssociations()
        bad = m.predict(cue())
        bad["observations"] = EqualitySpoof()
        with self.assertRaises(CuriosityError):
            m.learn(cue(), contact(), bad)
        self.assertEqual(m.updates, 0)

    def test_measurement_instrument_equality_spoof_rejected(self):
        m = ContactAssociations()
        feedback = contact()
        feedback["instrument"] = EqualitySpoof()
        with self.assertRaises(CuriosityError):
            m.learn(cue(), feedback, m.predict(cue()))
        self.assertEqual(m.updates, 0)

    def test_subclass_containers_are_not_trusted_as_native_evidence(self):
        class FakeDict(dict):
            pass

        m = ContactAssociations()
        with self.assertRaises(CuriosityError):
            m.predict(FakeDict(cue()))
        with self.assertRaises(CuriosityError):
            ContactAssociations.from_snapshot(FakeDict(m.snapshot()))
        with self.assertRaises(CuriosityError):
            m.learn(cue(), contact(), FakeDict(m.predict(cue())))
        self.assertEqual(m.updates, 0)


if __name__ == "__main__":
    unittest.main()
