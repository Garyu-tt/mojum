"""Fresh deterministic, generated motor QA distribution, distinct from fixed scenes.

This is source-visible, post-prior-research development evaluation. It is NOT
independently blinded, proof of navigation, or a benchmark reward function.
The policy sees only current proprioception; obstacles stay in world.advance.
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
import unittest

from mojum.developmental_policy import (
    ACTIONS, Explorer, Proprioception, STEP_METERS,
)
from mojum.developmental_policy_v2 import LocalCellExplorer, SCHEMA as V2_SCHEMA
from mojum.world_room import Room, advance, initial_state

FIXTURE_SEED = 0xC0111D3
# Pinned after the first exact-head green CI run (not an independent preregistration).
EXPECTED_FIXTURE_SHA256 = "3371da882a06b7bb5b03b966887d9378a9656f5ef756de45f7dcbc143e1df20e"
SCENES = 24
TICKS = 160
MODES = ("coarse_v1", "local_cell_v2", "frozen")
_DELTA = {
    "rest": (0., 0.), "x+": (STEP_METERS, 0.),
    "y+": (0., STEP_METERS), "x-": (-STEP_METERS, 0.),
    "y-": (0., -STEP_METERS),
}


def cases():
    """Make exactly 72 fixed-seed trials WITHOUT consulting their outcomes."""
    rng = random.Random(FIXTURE_SEED)
    for i in range(SCENES):
        width = rng.choice((.18, .24, .32, .44, .64, .88, 1.2))
        height = rng.choice((.18, .24, .32, .44, .64, .88, 1.2))
        starts = (
            (0., 0.), (width, height), (0., height),
            (width, 0.), (width / 2, height / 2),
            (rng.uniform(0., width), rng.uniform(0., height)),
        )
        start = starts[i % len(starts)]
        obstacles = []
        for _ in range(rng.randint(1, 4)):
            ow = rng.uniform(.025, min(.14, width / 3))
            oh = rng.uniform(.025, min(.14, height / 3))
            x = rng.uniform(0, width - ow)
            y = rng.uniform(0, height - oh)
            # Select only valid initial worlds; never reveal the obstacle
            # coordinates to any motor policy or use their collision oracle.
            if x - .015 <= start[0] <= x + ow + .015 and \
                    y - .015 <= start[1] <= y + oh + .015:
                continue
            obstacles.append((x, y, ow, oh))
        room = Room(width, height, tuple(obstacles))
        initial_state(room, *start)  # Fail immediately on invalid generation.
        regimes = (
            ("alternating", tuple(k % 2 == 0 for k in range(TICKS))),
            ("bursts", tuple(k % 17 in (0, 1, 2, 8, 13) for k in range(TICKS))),
            ("seeded", tuple(bool(rng.getrandbits(1)) for _ in range(TICKS))),
        )
        for pulse_name, pulses in regimes:
            yield f"gen-{i:02d}/{pulse_name}", room, start, pulses


def fingerprint():
    payload = [
        {
            "case": case_id,
            "room": {
                "width": room.width, "height": room.height,
                "obstacles": room.obstacles,
            },
            "start": start,
            "pulses": pulses,
        }
        for case_id, room, start, pulses in cases()
    ]
    wire = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")
    return hashlib.sha256(wire).hexdigest()


def run(room, start, pulses, mode):
    """One arm; no connection to SQLite, real devices, or external state."""
    if mode not in MODES:
        raise ValueError("unknown research arm")
    policy = (Explorer() if mode == "coarse_v1" else
              LocalCellExplorer() if mode == "local_cell_v2" else None)
    world = initial_state(room, *start)
    outcomes = []
    known_block = False
    longest = consecutive = 0
    locations = {tuple(round(v, 6) for v in start)}
    for tick, pulse in enumerate(pulses):
        sense = Proprioception(tick, world.revision, world.x, world.y, pulse)
        assert set(vars(sense)) == {"tick", "revision", "x", "y", "pulse"}
        assert type(sense) is Proprioception
        innate = ACTIONS[(tick + int(pulse)) % len(ACTIONS)]
        if policy is None:
            action = innate
            dx, dy = _DELTA[action]
            intent = None
        else:
            intent = policy.decide(sense)
            assert intent["expected_revision"] == world.revision
            action, dx, dy = intent["action"], intent["dx"], intent["dy"]
            if action != innate:
                assert known_block, "policy differed before seeing any collision"
        next_world, measured = advance(
            room, world, tick=tick + 1, dx=dx, dy=dy,
            expected_revision=world.revision,
        )
        if policy is not None:
            policy.observe(sense, intent, measured["blocked"])
        assert not (action == "rest" and measured["blocked"])
        known_block |= measured["blocked"]
        consecutive = consecutive + 1 if action == "rest" else 0
        longest = max(longest, consecutive)
        outcomes.append((action, measured["blocked"]))
        locations.add(tuple(round(v, 6) for v in measured["position"]))
        world = next_world
    if policy is not None:
        original = policy.snapshot()
        assert original["updates"] == TICKS
        assert type(policy).from_snapshot(original).snapshot() == original
    rest = sum(action == "rest" for action, _ in outcomes)
    blocked = sum(outcome for _, outcome in outcomes)
    attempted = TICKS - rest
    successful = attempted - blocked
    assert successful >= 0
    return {
        "attempted": attempted,
        "successful": successful,
        "blocked": blocked,
        "rest": rest,
        "longest_rest": longest,
        "stalled": longest >= TICKS // 4,
        "locations": len(locations),
        "first_action": outcomes[0][0],
        "schema": policy.snapshot()["schema"] if policy else None,
    }


def report():
    stats = {
        mode: {
            "blocked": 0, "rest": 0, "attempted": 0,
            "successful": 0, "stalled_cases": 0,
            "unique_position_sum": 0,
        }
        for mode in MODES
    }
    advantage = {"more_successful": 0, "same_successful": 0,
                 "less_successful": 0}
    counterexamples = []
    exposures = 0
    for case_id, room, start, pulses in cases():
        data = {mode: run(room, start, pulses, mode) for mode in MODES}
        assert {data[mode]["first_action"] for mode in MODES} == {
            ACTIONS[int(pulses[0]) % len(ACTIONS)]
        }
        if any(data[mode]["blocked"] for mode in MODES):
            exposures += 1
        for mode in MODES:
            sample = data[mode]
            for key in ("blocked", "rest", "attempted", "successful"):
                stats[mode][key] += sample[key]
            stats[mode]["stalled_cases"] += int(sample["stalled"])
            stats[mode]["unique_position_sum"] += sample["locations"]
        local, control = data["local_cell_v2"], data["frozen"]
        if local["successful"] > control["successful"]:
            advantage["more_successful"] += 1
        elif local["successful"] == control["successful"]:
            advantage["same_successful"] += 1
        else:
            advantage["less_successful"] += 1
        if (local["successful"] < control["successful"] or
                local["blocked"] > control["blocked"] or local["stalled"]):
            counterexamples.append({
                "case": case_id,
                "v2_successful": local["successful"],
                "frozen_successful": control["successful"],
                "v2_blocked": local["blocked"],
                "frozen_blocked": control["blocked"],
                "v2_stalled": local["stalled"],
            })
    return {
        "schema": "mojum.qa.r014.generated-distribution/1",
        "label": "SEEDED_DEVELOPMENT_CASES_NOT_INDEPENDENT_UNSEEN_PROOF",
        "seed": FIXTURE_SEED,
        "fingerprint_sha256": fingerprint(),
        "scene_count": SCENES,
        "pulse_regimes": 3,
        "case_count": SCENES * 3,
        "ticks_per_case": TICKS,
        "cases_with_measured_collision": exposures,
        "aggregates": stats,
        "v2_versus_frozen": advantage,
        "negative_cases": counterexamples,
        "conclusion": "DESCRIPTIVE_ONLY_NO_POLICY_PROMOTION",
    }


class GeneratedDistributionQA(unittest.TestCase):
    def test_geometry_is_valid_and_seeded_fingerprint_repeatable(self):
        self.assertEqual(len(tuple(cases())), 72)
        x = fingerprint()
        self.assertEqual(len(x), 64)
        self.assertEqual(x, fingerprint())
        self.assertEqual(x, EXPECTED_FIXTURE_SHA256)
        self.assertRegex(x, r"^[a-f0-9]{64}$")

    def test_measured_causal_loop_stable_across_regenerated_cases(self):
        # Genuine assertions about replay/causality/accounting, not a
        # post-selected threshold requiring the preferred arm to win.
        for case_id, room, start, pulses in cases():
            with self.subTest(case=case_id):
                output = {mode: run(room, start, pulses, mode) for mode in MODES}
                self.assertEqual(output["local_cell_v2"]["schema"], V2_SCHEMA)
                self.assertIsNone(output["frozen"]["schema"])
                self.assertEqual(len({v["first_action"] for v in output.values()}), 1)
                for metrics in output.values():
                    self.assertEqual(metrics["attempted"] + metrics["rest"], TICKS)
                    self.assertEqual(
                        metrics["successful"] + metrics["blocked"],
                        metrics["attempted"],
                    )
                    self.assertLessEqual(metrics["longest_rest"], metrics["rest"])
                    self.assertGreaterEqual(metrics["locations"], 1)
                    self.assertLessEqual(metrics["locations"], TICKS + 1)

    def test_report_keeps_all_negative_cases_and_no_private_trace(self):
        result = report()
        self.assertEqual(result["case_count"], 72)
        self.assertEqual(result["scene_count"], 24)
        self.assertEqual(result["ticks_per_case"], TICKS)
        self.assertEqual(sum(result["v2_versus_frozen"].values()), 72)
        for group in result["aggregates"].values():
            self.assertEqual(group["attempted"] + group["rest"], 72 * TICKS)
            self.assertEqual(group["successful"] + group["blocked"],
                             group["attempted"])
        wire = json.dumps(result, sort_keys=True, allow_nan=False)
        self.assertEqual(json.loads(wire), result)
        for forbidden in (
            '"trace"', '"room"', '"obstacles"', '"agent_id"', '"reward"',
            '"pain"', '"suffering"', '"valence"', '"private_event"',
        ):
            self.assertNotIn(forbidden, wire)
        self.assertEqual(result["conclusion"],
                         "DESCRIPTIVE_ONLY_NO_POLICY_PROMOTION")


if __name__ == "__main__":
    if sys.argv[1:] == ["--report"]:
        print(json.dumps(report(), sort_keys=True, allow_nan=False))
    else:
        unittest.main()
