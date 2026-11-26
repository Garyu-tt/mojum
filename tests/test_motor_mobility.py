"""Independent QA for the read-only mobility audit over committed motor frames."""

import copy
from contextlib import closing
import gc
from importlib.resources import files
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import weakref

from mojum.aquarium import Aquarium, AquariumError
from mojum.motor_mobility import MobilityAuditError, _committed_start, _verified_motion, audit_journal, summarize
from mojum.noosphere import NoosphereError, _verify_chain

DATA = files("mojum").joinpath("data")
SEED = json.loads(DATA.joinpath("genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads(DATA.joinpath("first_loop.json").read_text(encoding="utf-8"))


def frame(tick, kind, blocked, position, *, world_blocked=None):
    """Minimal committed-shape frame for the pure summarizer."""
    return {
        "schema": "mojum.aquarium.frame/1", "tick": tick, "time_ns": tick * 10,
        "genome_revision": 0, "phenotype": {}, "graph_execution_sha256": "0" * 64,
        "interoception": {}, "brain": {}, "learning_observer": None,
        "world": {"blocked": blocked if world_blocked is None else world_blocked,
                  "position": list(position)},
        "movement_request": {
            "east": {"dx": 0.125, "dy": 0.0},
            "north": {"dx": 0.0, "dy": 0.125},
            "west": {"dx": -0.125, "dy": 0.0},
            "south": {"dx": 0.0, "dy": -0.125},
            "rest": {"dx": 0.0, "dy": 0.0},
        }[kind],
        "physical_model": "NONE_FIXTURE_ONLY",
        "developmental_controller": {
            "schema": "mojum.developmental.motor-play/1",
            "intent": {
                "kind": kind,
                **({"east": {"dx": 0.125, "dy": 0.0},
                   "north": {"dx": 0.0, "dy": 0.125},
                   "west": {"dx": -0.125, "dy": 0.0},
                   "south": {"dx": 0.0, "dy": -0.125},
                   "rest": {"dx": 0.0, "dy": 0.0}}[kind]),
            }, "outcome": {"observed_blocked": blocked},
            "action_source": "internal_committed_prior_outcomes_only",
            "real_actuation": False,
        },
    }


def manifest(schema="mojum.developmental.motor-play/1"):
    return {
        "schema": "mojum.aquarium.bootstrap/1", "agent_id": SEED["genome_id"],
        "motor_mode": "developmental_fixture", "motor_policy_schema": schema,
        "temporal_schema": "mojum.temporal.motor-play-range/1",
    }


class SummarizeTests(unittest.TestCase):
    def test_counts_and_healthy_motion_do_not_trip_gates(self):
        frames = []
        x = 0.0
        for tick in range(30):
            blocked = tick in (5, 17)
            if not blocked:
                x += 0.125
            frames.append(frame(tick, "east", blocked, (x, 0.0)))
        report = summarize(manifest(), frames)
        self.assertEqual(report["frames"], 30)
        self.assertEqual(report["rest_actions"], 0)
        self.assertEqual(report["move_actions"], 30)
        self.assertEqual(report["blocked_attempts"], 2)
        self.assertEqual(report["successful_moves"], 28)
        self.assertEqual(report["distinct_positions"], 28)
        self.assertEqual(report["longest_rest_streak"], 0)
        self.assertEqual(report["path_length_m"], 27 * 0.125)
        self.assertEqual(report["coverage_span_m"], [27 * 0.125, 0.0])
        self.assertEqual(report["gates"], {"rest_collapse": False,
                                           "revisit_concentration": False})
        self.assertEqual(report["verdict"], "MOBILITY_OBSERVED")

    def test_permanent_rest_is_flagged_even_with_zero_collisions(self):
        frames = [frame(tick, "rest", False, (1.0, 2.0)) for tick in range(40)]
        report = summarize(manifest(), frames)
        self.assertEqual(report["blocked_attempts"], 0)
        self.assertEqual(report["successful_moves"], 0)
        self.assertEqual(report["idle_fraction"], 1.0)
        self.assertEqual(report["longest_rest_streak"], 40)
        self.assertTrue(report["gates"]["rest_collapse"])
        self.assertEqual(report["verdict"], "REST_COLLAPSE_SUSPECTED")

    def test_single_rest_before_the_streak_limit_is_not_collapse(self):
        frames = [frame(tick, "east", False, (tick * 0.125, 0.0)) for tick in range(20)]
        frames.append(frame(20, "rest", False, (19 * 0.125, 0.0)))
        report = summarize(manifest(), frames)
        self.assertEqual(report["idle_fraction"], 1 / 21)
        self.assertFalse(report["gates"]["rest_collapse"])
        self.assertEqual(report["verdict"], "MOBILITY_OBSERVED")

    def test_back_and_forth_oscillation_is_reported_as_revisit_concentration(self):
        frames = []
        for tick in range(40):
            position = (0.0, 0.0) if tick % 2 == 0 else (0.125, 0.0)
            frames.append(frame(tick, "west" if tick % 2 == 0 else "east",
                                False, position))
        report = summarize(manifest(), frames)
        self.assertEqual(report["distinct_positions"], 2)
        self.assertEqual(report["successful_moves"], 40)
        self.assertEqual(report["blocked_attempts"], 0)
        self.assertTrue(report["gates"]["revisit_concentration"])
        self.assertEqual(report["verdict"], "REVISIT_CONCENTRATION_SUSPECTED")

    def test_measured_motion_must_agree_with_reported_outcome(self):
        start = frame(0, "rest", False, (1.0, 1.0))
        inconsistent = (
            frame(1, "rest", False, (1.125, 1.0)),
            frame(1, "east", True, (1.125, 1.0)),
            frame(1, "east", False, (1.0, 1.0)),
        )
        for bad in inconsistent:
            with self.subTest(kind=bad["developmental_controller"]["intent"]["kind"],
                              blocked=bad["world"]["blocked"],
                              position=bad["world"]["position"]):
                with self.assertRaisesRegex(MobilityAuditError, "measured position"):
                    summarize(manifest(), [start, bad])

        consistent = [start, frame(1, "east", True, (1.0, 1.0)),
                      frame(2, "east", False, (1.125, 1.0))]
        report = summarize(manifest(), consistent)
        self.assertEqual(report["blocked_attempts"], 1)
        self.assertEqual(report["successful_moves"], 1)
        self.assertEqual(report["path_length_m"], 0.125)

    def test_blocked_or_short_stationary_history_is_not_mobility(self):
        # A zero-collision history is not proof of movement, and repeated
        # blocked attempts must not make a stationary policy look mobile.
        for actions in (
            [frame(tick, "east", True, (1.0, 2.0)) for tick in range(30)],
            [frame(tick, "rest", False, (1.0, 2.0)) for tick in range(4)],
            [frame(0, "east", False, (1.0, 2.0))],
        ):
            with self.subTest(frames=len(actions), kind=actions[0]["developmental_controller"]["intent"]["kind"]):
                report = summarize(manifest(), actions)
                self.assertEqual(report["path_length_m"], 0.0)
                self.assertEqual(report["verdict"], "NO_MEASURED_DISPLACEMENT")

    def test_committed_trajectory_must_match_action_and_initial_position(self):
        data = {**manifest(), "room": {"start": [1.0, 2.0]}}

        def verify(frames):
            previous = _committed_start(data)
            for item in frames:
                _, _, previous = _verified_motion(item, data["motor_policy_schema"], previous)

        def action(tick, kind, blocked, position, dx, dy):
            item = frame(tick, kind, blocked, position)
            item["movement_request"] = {"dx": dx, "dy": dy}
            item["developmental_controller"]["intent"].update(dx=dx, dy=dy)
            return item

        first = action(0, "east", False, (1.125, 2.0), 0.125, 0.0)
        second = action(1, "north", False, (1.125, 2.125), 0.0, 0.125)
        verify([first, second])
        report = summarize(data, [first, second])
        self.assertEqual(report["path_length_m"], 0.25)

        for position in ((100.0, 2.0), (0.875, 2.0), (1.0, 2.0)):
            with self.subTest(first_position=position):
                wrong = copy.deepcopy(first)
                wrong["world"]["position"] = list(position)
                with self.assertRaisesRegex(MobilityAuditError, "world position"):
                    verify([wrong])
                with self.assertRaisesRegex(MobilityAuditError, "world position"):
                    summarize(data, [wrong])

        blocked = action(0, "east", True, (1.125, 2.0), 0.125, 0.0)
        resting = action(0, "rest", False, (1.125, 2.0), 0.0, 0.0)
        for bad in (blocked, resting):
            with self.assertRaisesRegex(MobilityAuditError, "world position"):
                verify([bad])

        for field in ("movement_request", "intent"):
            wrong = copy.deepcopy(first)
            if field == "movement_request":
                wrong[field]["dx"] = 0.5
            else:
                wrong["developmental_controller"]["intent"]["dx"] = 0.0
            with self.subTest(field=field):
                with self.assertRaisesRegex(MobilityAuditError, "displacement"):
                    verify([wrong])

        with self.assertRaisesRegex(MobilityAuditError, "room start"):
            _committed_start(manifest())

    def test_empty_history_is_explicitly_not_averaged(self):
        report = summarize(manifest(), [])
        self.assertEqual(report["frames"], 0)
        self.assertEqual(report["idle_fraction"], 0.0)
        self.assertEqual(report["coverage_span_m"], [0.0, 0.0])
        self.assertFalse(report["gates"]["rest_collapse"])
        self.assertEqual(report["verdict"], "NO_FRAMES")

    def test_scripted_and_unknown_policies_are_rejected(self):
        for bad in ({}, {"schema": "mojum.aquarium.bootstrap/1"},
                    {**manifest(), "motor_mode": "scripted"},
                    {**manifest(), "motor_policy_schema": "mojum.other/1"},
                    {**manifest(), "motor_policy_schema": "mojum.developmental.motor-play/999"}):
            with self.assertRaises(MobilityAuditError):
                summarize(bad, [])

    def test_malformed_or_inconsistent_frames_fail_closed(self):
        good = frame(0, "east", False, (0.0, 0.0))
        mutations = [
            {**good, "tick": 1},                       # discontinuity
            {**good, "tick": True},                    # bool tick
            {**good, "schema": "mojum.aquarium.frame/2"},
            {**good, "world": {**good["world"], "blocked": True}},
            {**good, "world": {**good["world"], "position": [True, 0.0]}},
            {**good, "world": {**good["world"], "position": [float("nan"), 0.0]}},
            {**good, "world": {**good["world"], "position": [float("inf"), 0.0]}},
            {**good, "world": {**good["world"], "position": [0.0]}},
            {**good, "developmental_controller": {
                **good["developmental_controller"], "intent": {"kind": "oracle"}}},
            {**good, "developmental_controller": {
                **good["developmental_controller"], "outcome": {"observed_blocked": 1}}},
            {**good, "developmental_controller": {
                **good["developmental_controller"],
                "action_source": "external_script"}},
            {**good, "developmental_controller": {
                **good["developmental_controller"], "real_actuation": True}},
            {k: v for k, v in good.items() if k != "developmental_controller"},
        ]
        for bad in mutations:
            with self.assertRaises(MobilityAuditError):
                summarize(manifest(), [bad])
        with self.assertRaises(MobilityAuditError):
            summarize(manifest(), [frame(0, "rest", True, (0.0, 0.0))])


class JournalAuditTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = Path(scratch.name)
        self.db = self.dir / "mobility.sqlite3"

    def test_real_journal_is_audited_without_modifying_committed_bytes(self):
        with Aquarium(self.db, SEED, GRAPH, motor_mode="developmental_fixture",
                      sensory_mode="toy_probe") as aquarium:
            payloads = [aquarium.step() for _ in range(40)]
            checkpoint = aquarium.checkpoint()
        wire = self.db.read_bytes()
        report = audit_journal(self.db, external_expected_head=checkpoint["journal_head"])
        stored_manifest = {
            "schema": "mojum.aquarium.bootstrap/1",
            "motor_mode": report["motor_mode"],
            "motor_policy_schema": report["motor_policy_schema"],
            "temporal_schema": report["temporal_schema"],
            "room": aquarium.manifest["room"],
        }
        expected = summarize(stored_manifest, payloads)
        for key in ("frames", "rest_actions", "move_actions", "blocked_attempts",
                    "successful_moves", "longest_rest_streak", "distinct_positions",
                    "path_length_m", "verdict"):
            self.assertEqual(report[key], expected[key], key)
        self.assertEqual(report["events"], 41)
        self.assertEqual(report["integrity"], "chain-and-independent-head-checked")
        self.assertEqual(report["journal_head"], checkpoint["journal_head"])
        # A report is not an edit: the committed journal bytes are unchanged.
        self.assertEqual(self.db.read_bytes(), wire)

        # The read-only path also accepts a journal without an external anchor.
        unanchored = audit_journal(self.db)
        self.assertEqual(unanchored["integrity"], "chain-checked-not-authenticated")
        self.assertEqual(unanchored["verdict"], report["verdict"])

    def test_audit_releases_unused_frame_payload_before_next_verified_frame(self):
        with Aquarium(self.db, SEED, GRAPH, motor_mode="developmental_fixture") as aquarium:
            for _ in range(3):
                aquarium.step()

        class FrameMarker:
            pass

        def track_chain(db, owner, *, inspect_event, **options):
            previous_marker = None

            def track_frame(seq, event):
                nonlocal previous_marker
                if previous_marker is not None:
                    gc.collect()
                    self.assertIsNone(previous_marker(), "previous decoded frame is still retained")
                if event["topic"] == "aquarium.frame":
                    # The chain has verified this payload. Attach a lifetime
                    # marker to metadata ignored by the motion metrics.
                    marker = FrameMarker()
                    event["payload"]["brain"] = marker
                    previous_marker = weakref.ref(marker)
                inspect_event(seq, event)

            return _verify_chain(db, owner, inspect_event=track_frame, **options)

        with patch("mojum.motor_mobility._verify_chain", side_effect=track_chain):
            report = audit_journal(self.db)
        self.assertEqual(report["frames"], 3)

    def test_wrong_external_head_and_scripted_journal_are_rejected(self):
        with Aquarium(self.db, SEED, GRAPH, motor_mode="developmental_fixture") as aquarium:
            aquarium.step()
        with self.assertRaisesRegex(NoosphereError, "independent external anchor"):
            audit_journal(self.db, external_expected_head="0" * 64)
        with self.assertRaisesRegex(MobilityAuditError, "expected head"):
            audit_journal(self.db, external_expected_head="XYZ")

        scripted = self.dir / "scripted.sqlite3"
        with Aquarium(scripted, SEED, GRAPH) as aquarium:
            aquarium.step()
        wire = scripted.read_bytes()
        with self.assertRaisesRegex(MobilityAuditError, "no measured developmental motor policy"):
            audit_journal(scripted)
        self.assertEqual(scripted.read_bytes(), wire)

    def test_bootstrap_only_journal_reports_no_frames(self):
        with Aquarium(self.db, SEED, GRAPH, motor_mode="developmental_fixture"):
            pass
        report = audit_journal(self.db)
        self.assertEqual(report["events"], 1)
        self.assertEqual(report["frames"], 0)
        self.assertEqual(report["verdict"], "NO_FRAMES")

    def test_tampered_frame_payload_is_rejected_by_the_chain(self):
        with Aquarium(self.db, SEED, GRAPH, motor_mode="developmental_fixture") as aquarium:
            for _ in range(3):
                aquarium.step()
        with closing(sqlite3.connect(self.db)) as connection:
            with connection:
                # Remove the owner-facing append-only guards so this test can
                # simulate an external rewrite; the hash chain must still catch it.
                connection.execute("DROP TRIGGER IF EXISTS noosphere_no_update")
                row = connection.execute(
                    "SELECT seq, wire_json FROM events WHERE topic='aquarium.frame' "
                    "ORDER BY seq LIMIT 1").fetchone()
                payload = json.loads(row[1])
                payload["payload"]["movement_request"] = {"dx": 0.125, "dy": 0.125}
                connection.execute(
                    "UPDATE events SET wire_json=? WHERE seq=?",
                    (json.dumps(payload, sort_keys=True, separators=(",", ":")), row[0]),
                )
        with self.assertRaisesRegex(NoosphereError, "hash chain mismatch"):
            audit_journal(self.db)


if __name__ == "__main__":
    unittest.main()
