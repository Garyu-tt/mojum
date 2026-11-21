"""Anti-stall / rest-collapse contract for the opt-in Local Cell v5 policy.

The frozen v3/v4 selection rule can stop moving for an unbounded time: `rest`
is never blocked, so once every 4cm move has one blocked outcome in a cell,
`rest` wins forever. It also places `rest` inside the rotated action order, so
it rests on 1 of every 5 fresh cells even when all four moves are untried.

These tests are purely synthetic and make no claim about real navigation.
"""

from copy import deepcopy
import json
import tempfile
import unittest
from pathlib import Path

from mojum.aquarium import Aquarium, AquariumError, V3_MODE, V4_MODE, V5_MODE
from mojum.aquarium_wire import _check_policy_frame, _stored_policy, inspect_journal
from mojum.noosphere import LocalNoosphere, NoosphereError
from mojum.cell_policy_v3 import CellExplorerV3
from mojum.cell_policy_v5 import (
    MAX_CELLS,
    MAX_REST_STREAK,
    SCHEMA,
    CellExplorerV5,
    CellV5Error,
    summarize_transcript,
)
from mojum.epoch_sense_v2 import EPOCH_TICKS, EpochProprioception
from mojum.local_cell_sense import Proprioception, STEP_METERS
from mojum.world_room import Room, advance, initial_state

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
REVISION = "b" * 64
MOVE_ACTIONS = ("x+", "y+", "x-", "y-")


def cue(tick, x=0.0, y=0.0, pulse=False, revision=REVISION):
    return EpochProprioception(
        tick, tick // EPOCH_TICKS, tick % EPOCH_TICKS,
        revision, x, y, pulse,
    )


def drive(model, ticks, *, x=0.0, y=0.0, pulse_rule=lambda t: False,
          blocked_rule=lambda intent: intent["action"] != "rest",
          start=0, transcript=None):
    """Feed one model from a *virtual* blocked-move environment."""
    for tick in range(start, start + ticks):
        sense = cue(tick, x, y, pulse_rule(tick))
        intent = model.decide(sense)
        outcome = model.observe(sense, intent, blocked_rule(intent))
        if transcript is not None:
            transcript.append((intent, outcome))


def longest(values, predicate):
    best = run = 0
    for value in values:
        run = run + 1 if predicate(value) else 0
        best = max(best, run)
    return best


class FreshCellChoiceTests(unittest.TestCase):
    def test_v5_never_rests_while_an_untried_move_exists(self):
        model = CellExplorerV5()
        actions = []
        for tick in range(40):
            sense = cue(tick, pulse=tick % 2 == 0)
            intent = model.decide(sense)
            actions.append(intent["action"])
            model.observe(sense, intent, False)
        # Four distinct (tick+pulse) rotations cover the shift where v3/v4
        # puts "rest" first.
        self.assertNotIn("rest", actions)
        self.assertTrue({"x+", "y+", "x-", "y-"} <= set(actions))

    def test_v3_rests_on_a_rotation_that_starts_with_rest(self):
        # Guard the reported defect this policy version deliberately fixes.
        sense = Proprioception(0, REVISION, 0.0, 0.0, False)
        self.assertEqual(CellExplorerV3().decide(sense)["action"], "rest")
        self.assertNotEqual(CellExplorerV5().decide(cue(0))["action"], "rest")

    def test_decision_is_pure_and_repeatable(self):
        model = CellExplorerV5()
        sense = cue(0)
        self.assertEqual(model.decide(sense), model.decide(sense))
        self.assertEqual(model.updates, 0)

    def test_crossing_an_epoch_boundary_keeps_absolute_ticks(self):
        model = CellExplorerV5()
        observed = []
        for tick in range(EPOCH_TICKS + 4):
            sense = cue(tick, (tick % 3) * STEP_METERS)
            intent = model.decide(sense)
            if tick >= EPOCH_TICKS - 1:
                observed.append((tick, intent["epoch_index"]))
            model.observe(sense, intent, False)
        self.assertEqual(observed, [
            (EPOCH_TICKS - 1, 0), (EPOCH_TICKS, 1), (EPOCH_TICKS + 1, 1),
            (EPOCH_TICKS + 2, 1), (EPOCH_TICKS + 3, 1),
        ])
        self.assertEqual(model.last_tick, EPOCH_TICKS + 3)
        self.assertEqual(
            CellExplorerV5.from_snapshot(model.snapshot()).snapshot(),
            model.snapshot(),
        )


class RestCollapseTests(unittest.TestCase):
    def test_v3_collapses_to_unbounded_rest_in_a_blocked_cell(self):
        model = CellExplorerV3()
        actions = []
        for tick in range(60):
            sense = Proprioception(tick, REVISION, 0.0, 0.0, False)
            intent = model.decide(sense)
            actions.append(intent["action"])
            model.observe(sense, intent, intent["action"] != "rest")
        self.assertGreater(longest(actions, lambda a: a == "rest"), 40)
        self.assertGreaterEqual(actions.count("rest"), 50)

    def test_v5_bounds_the_longest_rest_streak(self):
        model = CellExplorerV5()
        actions = []
        for tick in range(200):
            sense = cue(tick)
            intent = model.decide(sense)
            outcome = model.observe(sense, intent, intent["action"] != "rest")
            actions.append(intent["action"])
            self.assertLessEqual(model.rest_streak, MAX_REST_STREAK)
            if intent["action"] == "rest":
                self.assertFalse(outcome["forced_probe"])
        self.assertLessEqual(
            longest(actions, lambda a: a == "rest"), MAX_REST_STREAK,
        )
        self.assertGreater(actions.count("rest"), 0)
        self.assertGreater(
            sum(1 for a in actions if a != "rest"), 4 * MAX_REST_STREAK,
        )
        # Repeated probes are legitimate evidence; the model still restores.
        self.assertEqual(
            CellExplorerV5.from_snapshot(model.snapshot()).snapshot(),
            model.snapshot(),
        )

    def test_forced_probe_reports_itself_in_the_measured_outcome(self):
        model = CellExplorerV5()
        forced = []
        for tick in range(60):
            sense = cue(tick)
            intent = model.decide(sense)
            outcome = model.observe(sense, intent, intent["action"] != "rest")
            if outcome["forced_probe"]:
                forced.append((intent["action"], outcome))
        self.assertTrue(forced)
        for action, outcome in forced:
            self.assertIn(action, MOVE_ACTIONS)
            self.assertTrue(outcome["measured_blocked"])
            self.assertGreaterEqual(outcome["observed_outcomes"], 1)
        self.assertEqual(model.forced_probes, len(forced))

    def test_sealed_room_keeps_task_attempts_with_v5(self):
        # Every 4cm step from the centre leaves this 2cm room, so each
        # attempted move is genuinely blocked and the position cannot change.
        world = Room(0.02, 0.02)
        state = initial_state(world, 0.01, 0.01)
        actions, blocked = [], []
        model = CellExplorerV5()
        for tick in range(120):
            sense = cue(tick, state.x, state.y, tick % 2 == 0,
                        revision=state.revision)
            intent = model.decide(sense)
            state, result = advance(
                world, state, tick=tick + 1, dx=intent["dx"],
                dy=intent["dy"], expected_revision=state.revision,
            )
            model.observe(sense, intent, result["blocked"])
            actions.append(intent["action"])
            blocked.append(result["blocked"])
        attempts = sum(1 for a in actions if a != "rest")
        self.assertGreater(attempts, 0)
        self.assertLessEqual(
            longest(actions, lambda a: a == "rest"), MAX_REST_STREAK,
        )
        self.assertEqual(attempts, sum(blocked))
        self.assertEqual(model.forced_probes, actions.count("rest") // MAX_REST_STREAK)
        self.assertEqual((state.x, state.y), (0.01, 0.01))


class RetentionTests(unittest.TestCase):
    def test_exceeds_512_cells_without_growth_and_with_anti_stall(self):
        model = CellExplorerV5()
        first_eviction = None
        for tick in range(2400):
            sense = cue(tick, (tick + 1) * STEP_METERS, 0.0, tick % 7 == 0)
            intent = model.decide(sense)
            outcome = model.observe(sense, intent, False)
            self.assertLessEqual(len(model.records), MAX_CELLS)
            if tick == MAX_CELLS:
                first_eviction = outcome
        self.assertEqual(first_eviction["evicted_cell"], "1:0")
        self.assertEqual(model.updates, 2400)
        self.assertEqual(model.evictions, 2400 - MAX_CELLS)
        self.assertEqual(model.forgotten_observations, 2400 - MAX_CELLS)
        self.assertEqual(len(model.records), MAX_CELLS)

    def test_restart_is_bit_identical_to_continuous_run(self):
        first = CellExplorerV5()
        second = CellExplorerV5()
        for tick in range(150):
            sense = cue(tick, (tick % 9) * STEP_METERS,
                        (tick % 5) * STEP_METERS, tick % 2 == 0)
            blocked = tick % 3 == 0
            for model in (first, second):
                intent = model.decide(sense)
                model.observe(sense, intent, blocked)
            if tick == 73:
                second = CellExplorerV5.from_snapshot(second.snapshot())
        self.assertEqual(first.snapshot(), second.snapshot())

    def test_snapshot_rejects_rest_streak_without_observed_rests(self):
        model = CellExplorerV5()
        sense = cue(0)
        intent = model.decide(sense)
        self.assertNotEqual(intent["action"], "rest")
        model.observe(sense, intent, False)
        forged = model.snapshot()
        forged["rest_streak"] = 1
        # All generic counters and accounting still add up, but the final
        # cell has no measured rest history to support this streak.
        with self.assertRaisesRegex(CellV5Error, "rest streak exceeds"):
            CellExplorerV5.from_snapshot(forged)
        self.assertEqual(model.rest_streak, 0)

    def test_snapshot_rejects_rest_without_four_measured_blocks(self):
        model = CellExplorerV5()
        drive(model, 5)
        checkpoint = model.snapshot()
        self.assertEqual(checkpoint["rest_streak"], 1)
        self.assertEqual(CellExplorerV5.from_snapshot(checkpoint).snapshot(), checkpoint)

        forged = deepcopy(checkpoint)
        forged["records"]["0:0"]["x+"][1] = 0
        with self.assertRaisesRegex(CellV5Error, "rest streak requires four measured blocks"):
            CellExplorerV5.from_snapshot(forged)

    def test_snapshot_requires_enough_ticks_for_forced_probes(self):
        model = CellExplorerV5()
        drive(model, 7)
        with self.assertRaisesRegex(CellV5Error, "insufficient ticks for forced probes"):
            CellExplorerV5.from_snapshot(dict(model.snapshot(), forced_probes=1))

        drive(model, 1, start=7)
        checkpoint = model.snapshot()
        self.assertEqual(checkpoint["forced_probes"], 1)
        self.assertEqual(CellExplorerV5.from_snapshot(checkpoint).snapshot(), checkpoint)

    def test_snapshot_rejects_forged_or_cross_version_envelopes(self):
        model = CellExplorerV5()
        for tick in range(40):
            sense = cue(tick)
            intent = model.decide(sense)
            model.observe(sense, intent, intent["action"] != "rest")
        good = model.snapshot()
        self.assertEqual(CellExplorerV5.from_snapshot(good).snapshot(), good)
        mutations = (
            ("schema", "mojum.developmental.local-cell/4"),
            ("schema", "mojum.developmental.local-cell/3"),
            ("cell_size_m", 0.5),
            ("updates", good["updates"] + 1),
            ("last_tick", True),
            ("last_epoch_index", -1),
            ("last_epoch_index", 1),
            ("rest_streak", -1),
            ("rest_streak", MAX_REST_STREAK + 1),
            ("rest_streak", True),
            ("forced_probes", -1),
            ("forced_probes", good["updates"] + 1),
            ("evictions", -1),
            ("forgotten_observations", 1),
            ("records", {"00:0": {"x+": [1, 0]}}),
            ("records", {}),
            ("last_seen_at", {}),
        )
        for field, replacement in mutations:
            with self.subTest(field=field, value=replacement):
                changed = dict(good, **{field: replacement})
                with self.assertRaises(CellV5Error):
                    CellExplorerV5.from_snapshot(changed)
        with self.assertRaises(CellV5Error):
            CellExplorerV5.from_snapshot(dict(good, **{"extra": 1}))

    def test_snapshot_rejects_impossible_retention_and_tick_history(self):
        # Mirrored v3/v4 invariants: each observation belongs to exactly one
        # absolute tick and a 512-cell table must precede every eviction.
        common = {
            "schema": SCHEMA,
            "cell_size_m": STEP_METERS,
            "last_epoch_index": 0,
            "rest_streak": 0,
            "forced_probes": 0,
        }
        forged = {
            "two_cells_on_one_tick": {
                **common, "last_tick": 1, "updates": 2,
                "records": {
                    "1:1": {"rest": [1, 0]},
                    "2:1": {"rest": [1, 0]},
                },
                "last_seen_at": {"1:1": 1, "2:1": 1},
                "evictions": 0, "forgotten_observations": 0,
            },
            "latest_observation_before_current_tick": {
                **common, "last_tick": 1, "updates": 2,
                "records": {"1:1": {"rest": [2, 0]}},
                "last_seen_at": {"1:1": 0},
                "evictions": 0, "forgotten_observations": 0,
            },
            "eviction_before_512_distinct_cells": {
                **common, "last_tick": 1, "updates": 2,
                "records": {"1:1": {"rest": [1, 0]}},
                "last_seen_at": {"1:1": 1},
                "evictions": 1, "forgotten_observations": 1,
            },
            "eviction_must_preserve_a_full_table": {
                **common, "last_tick": 512, "updates": 513,
                "records": {"1:1": {"rest": [1, 0]}},
                "last_seen_at": {"1:1": 512},
                "evictions": 1, "forgotten_observations": 512,
            },
            "cell_measured_more_often_than_ticks": {
                **common, "last_tick": 99, "updates": 100,
                "records": {
                    "0:0": {"rest": [5, 0]},
                    "2500:0": {"rest": [95, 0]},
                },
                "last_seen_at": {"0:0": 0, "2500:0": 99},
                "evictions": 0, "forgotten_observations": 0,
            },
            "updates_without_any_live_cell": {
                **common, "last_tick": 0, "updates": 1,
                "records": {}, "last_seen_at": {},
                "evictions": 1, "forgotten_observations": 1,
            },
        }
        for name, snapshot in forged.items():
            with self.subTest(name=name), self.assertRaises(CellV5Error):
                CellExplorerV5.from_snapshot(snapshot)
        model = CellExplorerV5()
        for tick in range(2):
            sense = cue(tick, tick * STEP_METERS, STEP_METERS)
            model.observe(sense, model.decide(sense), False)
        self.assertEqual(
            CellExplorerV5.from_snapshot(model.snapshot()).snapshot(),
            model.snapshot(),
        )

    def test_restore_detaches_from_original_snapshot(self):
        model = CellExplorerV5()
        sense = cue(0)
        model.observe(sense, model.decide(sense), True)
        checkpoint = model.snapshot()
        alias = CellExplorerV5.from_snapshot(checkpoint)
        alias.records.clear()
        alias.last_seen_at.clear()
        self.assertEqual(model.snapshot(), checkpoint)

    def test_observe_is_atomic_on_forgery(self):
        model = CellExplorerV5()
        sense = cue(0)
        intent = model.decide(sense)
        before = model.snapshot()
        with self.assertRaises(CellV5Error):
            model.observe(sense, dict(intent, action="rest"), False)
        with self.assertRaises(CellV5Error):
            model.observe(sense, intent, "blocked")
        with self.assertRaises(CellV5Error):
            model.observe(cue(5), intent, False)
        self.assertEqual(model.snapshot(), before)

    def test_observe_rejects_equal_but_differently_typed_intent_fields(self):
        model = CellExplorerV5()
        sense = cue(0)
        intent = model.decide(sense)
        before = model.snapshot()
        for field, value in (("forced_probe", 0), ("tick", False), ("epoch_index", 0.0)):
            with self.subTest(field=field), self.assertRaises(CellV5Error):
                model.observe(sense, dict(intent, **{field: value}), False)
            self.assertEqual(model.snapshot(), before)
        self.assertIs(model.observe(sense, intent, False)["forced_probe"], False)


class TranscriptSummaryTests(unittest.TestCase):
    def test_empty_transcript_has_zero_idle_fraction(self):
        report = summarize_transcript([])
        self.assertEqual(report["ticks"], 0)
        self.assertEqual(report["attempts"], 0)
        self.assertEqual(report["idle_fraction"], 0.0)
        self.assertEqual(report["longest_rest_streak"], 0)

    def test_summary_matches_hand_counted_transcript(self):
        transcript = []
        drive(CellExplorerV5(), 12, transcript=transcript)
        report = summarize_transcript(transcript)
        actions = [item[0]["action"] for item in transcript]
        blocked = [item[1]["measured_blocked"] for item in transcript]
        self.assertEqual(report["ticks"], 12)
        self.assertEqual(report["attempts"], sum(a != "rest" for a in actions))
        self.assertEqual(report["rest_steps"], actions.count("rest"))
        self.assertEqual(report["blocked"], sum(blocked))
        self.assertEqual(
            report["successful_moves"], sum(1 for a, b in zip(actions, blocked)
                                            if a != "rest" and not b),
        )
        self.assertEqual(
            report["longest_rest_streak"],
            longest(actions, lambda a: a == "rest"),
        )
        self.assertAlmostEqual(report["idle_fraction"], actions.count("rest") / 12)

    def test_summary_rejects_malformed_rows(self):
        for bad in (
            17,  # A non-iterable must raise CellV5Error, not TypeError.
            [17],  # A non-sequence row must not reach len(row).
            [None],
            [("x+", {"measured_blocked": False})],
            [({"action": "up"}, {"measured_blocked": False})],
            [({"action": "x+"}, {"measured_blocked": 1})],
            [({"action": "x+"}, {})],
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(CellV5Error):
                    summarize_transcript(bad)


class V5AquariumVersionGateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "v5.sqlite3"

    def aquarium(self, path=None, mode=V5_MODE):
        return Aquarium(path or self.db, SEED, GRAPH, motor_mode=mode)

    def test_v5_journal_replays_identically_and_is_identifiable(self):
        with self.aquarium() as rover:
            for _ in range(60):
                rover.step()
            expected = rover.checkpoint()
        self.assertEqual(expected["developmental_state"]["schema"], SCHEMA)
        self.assertEqual(inspect_journal(self.db)["format"], "local_cell_v5")
        with self.aquarium() as resumed:
            self.assertEqual(resumed.checkpoint(), expected)
            self.assertLessEqual(
                resumed.checkpoint()["developmental_state"]["rest_streak"],
                MAX_REST_STREAK,
            )
            resumed.step()

    def test_same_seed_and_ticks_are_deterministic_across_journals(self):
        other = self.root / "twin.sqlite3"
        receipts = []
        for path in (self.db, other):
            with self.aquarium(path) as rover:
                for _ in range(45):
                    rover.step()
                receipts.append(rover.checkpoint())
        self.assertEqual(receipts[0], receipts[1])

    def test_wrong_policy_journals_are_refused_before_append(self):
        with self.aquarium() as rover:
            for _ in range(10):
                rover.step()
        before = self.db.read_bytes()
        for wrong in (V4_MODE, V3_MODE, "local_cell_v2b"):
            with self.subTest(mode=wrong):
                with self.assertRaisesRegex(AquariumError, "mismatch"):
                    self.aquarium(mode=wrong)
        self.assertEqual(self.db.read_bytes(), before)

    def test_v4_sensory_v2_remains_replayable_after_v5_integration(self):
        # #377 is already shipped; adding v5 must not disable its ABI.
        path = self.root / "canonical-v4-sensory-v2.sqlite3"
        kwargs = dict(motor_mode=V4_MODE, sensory_mode="toy_probe_v2",
                      neural_mode="disabled")
        with Aquarium(path, SEED, GRAPH, **kwargs) as rover:
            for _ in range(5):
                rover.step()
            expected = rover.checkpoint()
        self.assertEqual(inspect_journal(path)["format"],
                         "local_cell_v4+toy_probe_v2")
        with Aquarium(path, SEED, GRAPH, **kwargs) as resumed:
            self.assertEqual(resumed.checkpoint(), expected)

    def test_v5_rejects_unpinned_sensory_v2_before_birth(self):
        path = self.root / "invalid-v5-sensory.sqlite3"
        with self.assertRaises(AquariumError):
            Aquarium(path, SEED, GRAPH, motor_mode=V5_MODE,
                     sensory_mode="toy_probe_v2")
        self.assertFalse(path.exists())

    def test_wire_inspector_rejects_v5_frame_with_unpinned_toy_contact(self):
        with self.aquarium() as rover:
            rover.step()
            bootstrap, frame = (
                row["event"] for row in rover.store.iter_events(include_nonpublic=True)
            )
        with Aquarium(self.root / "v4-sensory.sqlite3", SEED, GRAPH,
                      motor_mode=V4_MODE, sensory_mode="toy_probe_v2") as rover:
            frame["payload"]["toy_contact"] = rover.step()["toy_contact"]
        mixed_path = self.root / "mixed-v5.sqlite3"
        with LocalNoosphere(mixed_path, bootstrap["agent_id"]) as store:
            head = store.append(bootstrap, expected_head=store.head())
            head = store.append(frame, expected_head=head)
            self.assertEqual(store.verify_chain()["head"], head)
        before = mixed_path.read_bytes()
        with self.assertRaisesRegex(NoosphereError, "unexpected toy contact"):
            inspect_journal(mixed_path, external_expected_head=head)
        self.assertEqual(mixed_path.read_bytes(), before)

    def test_v5_epoch_frame_inspection_rejects_forged_clock(self):
        with self.aquarium() as rover:
            frame = rover.step()
            _, contract, temporal, sensory = _stored_policy(rover.manifest)
        _check_policy_frame(frame, contract, temporal, sensory)
        forged = deepcopy(frame)
        forged["developmental"]["observation"]["epoch_tick"] += 1
        with self.assertRaisesRegex(NoosphereError, "epoch"):
            _check_policy_frame(forged, contract, temporal, sensory)

    def test_existing_v4_journal_cannot_be_relabelled_as_v5(self):
        with self.aquarium(mode=V4_MODE) as rover:
            for _ in range(10):
                rover.step()
        before = self.db.read_bytes()
        with self.assertRaisesRegex(AquariumError, "mismatch"):
            self.aquarium()
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
