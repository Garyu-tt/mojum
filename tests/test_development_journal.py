"""R0.8 grounded developmental diary and evidence ownership tests."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.development_journal import DiaryError, from_sqlite, summarize
from mojum.infant_play import InfantPlay
from mojum.world_room import Room


ROOT = Path(__file__).resolve().parent.parent
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class DevelopmentalDiaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "baby.sqlite3"

    def run_infant(self, steps=80, **kw):
        with InfantPlay(self.db, GRAPH, **kw) as child:
            traces = [child.step()["viewer"] for _ in range(steps)]
            before = child.checkpoint()
        return traces, before

    def test_observable_private_events_are_not_invented(self):
        frames, _ = self.run_infant(120)
        report = from_sqlite(self.db)
        self.assertEqual(report["frames_analyzed"], 120)
        self.assertEqual(report["schema"], "mojum.development.diary/1")
        self.assertEqual(report["last_tick"], 119)
        self.assertEqual(report["source_integrity"], "local_chain_verified_not_authenticated")
        self.assertEqual(report["status"], "OBSERVABLE_SYNTHETIC_DEVELOPMENT_NOT_PERSONALITY_MEASUREMENT")
        kinds = {m["kind"] for m in report["milestones"]}
        self.assertIn("first_observation", kinds)
        self.assertIn("first_repeated_action", kinds)
        self.assertIn("first_successful_motion", kinds)
        self.assertEqual(report["milestones"][0]["tick"], 0)
        for event in report["milestones"]:
            tick = event["tick"]
            source = frames[tick]
            self.assertEqual(event["event_id"], f"infant-frame-{tick:012d}")
            self.assertEqual(event["action"], source["experiment"]["action"])
            self.assertEqual(event["evidence"]["observed_effect"],
                             source["outcome"]["actual_effect"])
            self.assertEqual(len(event["event_hash"]), 64)
        self.assertEqual(sum(d["attempts"] for d in report["action_experience"].values()), 120)
        self.assertEqual(report["inference_scope"], "causal_local_action_effect_only_not_thoughts")

    def test_diarized_source_does_not_mutate_agent_or_private_journal(self):
        _, before = self.run_infant(40)
        first = from_sqlite(self.db)
        second = from_sqlite(self.db)
        self.assertEqual(first, second)
        with InfantPlay(self.db, GRAPH) as child:
            self.assertEqual(child.checkpoint(), before)
            self.assertEqual(child.store.dataset()["projection"]["items"], [])
            self.assertEqual(child.store.count(), 41)
            child.step()
            self.assertEqual(child.model.updates, 41)
        after = from_sqlite(self.db)
        self.assertEqual(after["frames_analyzed"], 41)
        self.assertEqual(after["milestones"], first["milestones"])

    def test_raw_jsonl_has_no_unearned_hash_validation(self):
        trace, _ = self.run_infant(20)
        report = summarize(trace)
        self.assertEqual(report["source_integrity"], "unverified_local_trace")
        self.assertNotIn("journal_head", report)
        self.assertTrue(all(m["event_hash"] is None for m in report["milestones"]))
        with self.assertRaises(DiaryError):
            summarize(trace, source_integrity="local_chain_verified_not_authenticated")

    def test_first_blocked_motion_has_exact_event_evidence(self):
        wall = Room(4, 4, ((1.04, 1.0, .1, 2),))
        frames, _ = self.run_infant(220, room=wall, start=(1, 2))
        diary = from_sqlite(self.db)
        blocked = next(m for m in diary["milestones"] if m["kind"] == "first_blocked_motion")
        self.assertTrue(frames[blocked["tick"]]["outcome"]["blocked"])
        self.assertEqual(blocked["evidence"]["observed_effect"], 0.0)
        self.assertEqual(blocked["evidence"]["blocked"], True)

    def test_false_claimed_sentience_or_forged_score_rejected(self):
        traces, _ = self.run_infant(12)
        for change in (
            {"path": ("source",), "value": "SENTIENT_THOUGHT_STREAM"},
            {"path": ("inference", "not"), "value": "this_is_a_real_thought"},
            {"path": ("spectator_scene", "available_to_agent"), "value": True},
            {"path": ("assessment", "metric"), "value": "personality_score"},
            {"path": ("assessment", "score_0_to_5"), "value": 99},
            {"path": ("memory", "prediction_error"), "value": float("nan")},
            {"path": ("memory", "times_tried"), "value": -1},
        ):
            bad = copy.deepcopy(traces)
            obj = bad[0]
            for key in change["path"][:-1]:
                obj = obj[key]
            obj[change["path"][-1]] = change["value"]
            with self.subTest(change=change["path"]):
                with self.assertRaises(DiaryError):
                    summarize(bad)

    def test_rejects_missing_and_out_of_order_evidence(self):
        trace, _ = self.run_infant(15)
        with self.assertRaises(DiaryError):
            summarize([])
        with self.assertRaises(DiaryError):
            summarize(trace[:4] + trace[5:])
        with self.assertRaises(DiaryError):
            summarize(trace + [trace[-1]])
        with self.assertRaises(DiaryError):
            summarize(trace, event_hashes=["abc"] * len(trace))
        with self.assertRaises(DiaryError):
            from_sqlite(self.db.with_name("not-existing.sqlite3"))
        self.assertFalse(self.db.with_name("not-existing.sqlite3").exists())

    def test_cli_matches_api_and_not_a_public_export(self):
        self.run_infant(16)
        cmd = [sys.executable, "-m", "mojum.development_journal", "--db", str(self.db)]
        output = json.loads(subprocess.check_output(cmd, text=True))
        self.assertEqual(output, from_sqlite(self.db))
        self.assertTrue(all("event_hash" in m for m in output["milestones"]))
        self.assertNotIn("frames", output)
        self.assertNotIn("raw_journal", output)

    def test_independent_babies_start_without_common_life_history(self):
        self.run_infant(12, seed=2027)
        one = from_sqlite(self.db)
        other = self.db.with_name("other.sqlite3")
        with InfantPlay(other, GRAPH, seed=5) as child:
            child.step()
        two = from_sqlite(other)
        self.assertEqual(one["frames_analyzed"], 12)
        self.assertEqual(two["frames_analyzed"], 1)
        self.assertEqual(two["milestones"][0]["event_id"], "infant-frame-000000000000")
        self.assertNotEqual(one["journal_head"], two["journal_head"])


if __name__ == "__main__":
    unittest.main()
