import copy
import json
import unittest

from mojum.genesis import ACQUISITION_SCHEMA, sha256
from mojum.twin_study import (
    MEASUREMENT_SCHEMA, MEETING_SCHEMA, TwinStudyError, compare, create_study,
    record_acquisition, record_measurement, record_meeting, validate_study,
)

SEED = {"schema": "mojum.genesis.genotype/1", "genome_id": "template-only",
        "revision": "fixture-r1", "inherited": {
            "heartbeat_bpm": 60, "body_scale_milli": 1000, "sensory_gain_milli": 1000}}


def measurement(agent="twin-a", eid="m1", tick=1, domain="music", score=500):
    return {"schema": MEASUREMENT_SCHEMA, "event_id": eid, "agent_id": agent,
            "tick": tick, "domain": domain, "score_milli": score, "source": "synthetic:test"}


def meeting(mid="visit-1", tick=3):
    return {"schema": MEETING_SCHEMA, "meeting_id": mid, "tick": tick,
            "activity": "cooperative-game"}


def acquisition(agent="twin-a", eid="a1", tick=4, delta=8):
    return {"schema": ACQUISITION_SCHEMA, "event_id": eid, "genome_id": agent,
            "gene": "heartbeat_bpm", "delta": delta,
            "valid_from_tick": tick, "observed_at_ns": 100,
            "recorded_at_ns": 100, "source": "synthetic:test", "evidence": "fixture-only"}


class TwinStudyTests(unittest.TestCase):
    def setUp(self):
        self.study = create_study(SEED, left_id="twin-a", left_name="Aster",
                                  right_id="twin-b", right_name="Birch")

    def test_identical_start_and_distinct_human_names(self):
        result = compare(self.study, tick=0)
        self.assertEqual(result["gene_delta_left_minus_right"], {
            "body_scale_milli": 0, "heartbeat_bpm": 0, "sensory_gain_milli": 0})
        self.assertEqual([p["name"] for p in result["subjects"]], ["Aster", "Birch"])
        self.assertEqual([p["agent_id"] for p in result["subjects"]], ["twin-a", "twin-b"])
        self.assertEqual(result["classification"], "observer_scored_synthetic_fixture_not_cognitive_evidence")
        self.assertNotEqual(self.study["participants"][0]["genome"]["seed"]["genome_id"], SEED["genome_id"])

    def test_source_seed_and_returned_copy_do_not_alias(self):
        original = copy.deepcopy(SEED)
        child = validate_study(self.study)
        child["participants"][0]["genome"]["seed"]["inherited"]["heartbeat_bpm"] = 130
        self.assertEqual(SEED, original)
        self.assertEqual(compare(self.study, tick=0)["subjects"][0]["genes"]["heartbeat_bpm"], 60)

    def test_isolated_exposure_and_scores(self):
        first = record_measurement(self.study, measurement(), expected_revision=self.study["revision"])
        self.assertEqual(len(self.study["participants"][0]["measurements"]), 0)
        self.assertEqual(len(first["participants"][1]["measurements"]), 0)
        second = record_measurement(first, measurement("twin-b", "b1", 1, score=350),
                                    expected_revision=first["revision"])
        result = compare(second, tick=1)
        self.assertEqual(result["measured_score_delta_left_minus_right"], {"music": 150})
        self.assertEqual(result["gene_delta_left_minus_right"]["heartbeat_bpm"], 0)

    def test_missing_comparable_score_is_null_not_zero(self):
        first = record_measurement(self.study, measurement(), expected_revision=self.study["revision"])
        result = compare(first, tick=1)
        self.assertIsNone(result["measured_score_delta_left_minus_right"]["music"])
        self.assertEqual(result["subjects"][1]["scores_milli"], {})

    def test_genome_acquisition_affects_one_subject_only(self):
        first = record_acquisition(self.study, "twin-a", acquisition(), expected_revision=self.study["revision"])
        early = compare(first, tick=3)
        later = compare(first, tick=4)
        self.assertEqual(early["gene_delta_left_minus_right"]["heartbeat_bpm"], 0)
        self.assertEqual(later["gene_delta_left_minus_right"]["heartbeat_bpm"], 8)
        self.assertEqual(later["subjects"][1]["acquired_count"], 0)

    def test_meeting_is_marker_not_memory_merge(self):
        first = record_measurement(self.study, measurement(), expected_revision=self.study["revision"])
        both = record_meeting(first, meeting(), expected_revision=first["revision"])
        self.assertEqual(compare(both, tick=2)["meetings_through_tick"], 0)
        self.assertEqual(compare(both, tick=3)["meetings_through_tick"], 1)
        self.assertEqual(len(both["participants"][0]["measurements"]), 1)
        self.assertEqual(len(both["participants"][1]["measurements"]), 0)
        self.assertEqual(both["participants"][1]["genome"], first["participants"][1]["genome"])

    def test_full_snapshot_validates_and_roundtrips(self):
        first = record_measurement(self.study, measurement(), expected_revision=self.study["revision"])
        second = record_meeting(first, meeting(), expected_revision=first["revision"])
        checked = validate_study(json.loads(json.dumps(second)))
        self.assertEqual(checked, second)
        self.assertEqual(compare(checked, tick=6), compare(second, tick=6))

    def test_reject_stale_revision_and_duplicate_event(self):
        first = record_measurement(self.study, measurement(), expected_revision=self.study["revision"])
        with self.assertRaisesRegex(TwinStudyError, "CAS"):
            record_measurement(first, measurement(eid="m2"), expected_revision=self.study["revision"])
        with self.assertRaisesRegex(TwinStudyError, "duplicate"):
            record_measurement(first, measurement(), expected_revision=first["revision"])

    def test_reject_rewritten_snapshot_and_cross_subject_genome(self):
        changed = copy.deepcopy(self.study)
        changed["participants"][0]["name"] = "Changed"
        with self.assertRaisesRegex(TwinStudyError, "checksum"):
            validate_study(changed)
        changed["revision"] = sha256({k: v for k, v in changed.items() if k != "revision"})
        changed["participants"][0]["genome"] = copy.deepcopy(changed["participants"][1]["genome"])
        changed["revision"] = sha256({k: v for k, v in changed.items() if k != "revision"})
        with self.assertRaisesRegex(TwinStudyError, "identity mismatch"):
            validate_study(changed)

    def test_reject_nonidentical_inherited_even_with_recomputed_checksums(self):
        changed = copy.deepcopy(self.study)
        subject = changed["participants"][1]
        subject["genome"]["seed"]["inherited"]["heartbeat_bpm"] = 61
        subject["genome"]["revision"] = sha256({"seed": subject["genome"]["seed"], "events": []})
        changed["revision"] = sha256({k: v for k, v in changed.items() if k != "revision"})
        with self.assertRaisesRegex(TwinStudyError, "same inherited"):
            validate_study(changed)

    def test_backdating_prevented(self):
        first = record_measurement(self.study, measurement(tick=10), expected_revision=self.study["revision"])
        with self.assertRaisesRegex(TwinStudyError, "backdate"):
            record_meeting(first, meeting(tick=9), expected_revision=first["revision"])
        met = record_meeting(first, meeting(tick=10), expected_revision=first["revision"])
        with self.assertRaisesRegex(TwinStudyError, "backdate"):
            record_measurement(met, measurement(eid="m2", tick=9), expected_revision=met["revision"])
        with self.assertRaisesRegex(TwinStudyError, "may not rewrite"):
            record_acquisition(met, "twin-a", acquisition(tick=9), expected_revision=met["revision"])

    def test_reject_malformed_measurement_or_names(self):
        for bad in (True, -1, 1001, 0.5, "500"):
            with self.subTest(score=bad), self.assertRaises(TwinStudyError):
                record_measurement(self.study, measurement(score=bad), expected_revision=self.study["revision"])
        with self.assertRaises(TwinStudyError):
            create_study(SEED, left_id="twin-a", left_name="Same", right_id="twin-b", right_name="Same")
        invalid = measurement()
        invalid["arbitrary"] = "privileged"
        with self.assertRaises(TwinStudyError):
            record_measurement(self.study, invalid, expected_revision=self.study["revision"])

    def test_meeting_ids_cannot_repeat(self):
        first = record_meeting(self.study, meeting(), expected_revision=self.study["revision"])
        with self.assertRaisesRegex(TwinStudyError, "duplicate"):
            record_meeting(first, meeting(), expected_revision=first["revision"])


if __name__ == "__main__":
    unittest.main()
