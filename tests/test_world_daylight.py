"""Hermetic CPython 3.11+ tests for synthetic logical clock daylight."""
import json
import unittest

from mojum.world_daylight import (
    MAX_TICK, DaylightCycle, DaylightState, advance, initial_state,
    observation, restore, snapshot,
)


class DaylightTests(unittest.TestCase):
    def setUp(self):
        self.cycle = DaylightCycle(2, 3, 2, 3)

    def test_exact_phase_boundaries(self):
        self.assertEqual(
            [observation(self.cycle, tick=i)["phase"] for i in range(11)],
            ["dawn", "dawn", "day", "day", "day", "dusk", "dusk", "night", "night", "night", "dawn"])
        self.assertEqual([observation(self.cycle, tick=i)["light_permille"] for i in range(10)],
                         [500, 1000, 1000, 1000, 1000, 500, 0, 0, 0, 0])

    def test_transition_is_one_tick_and_cas_protected(self):
        state = initial_state(self.cycle)
        observations = []
        for tick in range(1, 21):
            state, obs = advance(self.cycle, state, tick=tick, expected_revision=state.revision)
            observations.append(obs)
        self.assertEqual(state.tick, 20)
        self.assertEqual([o["tick"] for o in observations if o["phase_changed"]], [2, 5, 7, 10, 12, 15, 17, 20])
        self.assertEqual(observations[-1]["cycle_index"], 2)

    def test_replay_same_bytes(self):
        a = initial_state(self.cycle)
        left = advance(self.cycle, a, tick=1, expected_revision=a.revision)
        right = advance(self.cycle, a, tick=1, expected_revision=a.revision)
        self.assertEqual(left, right)
        self.assertEqual(snapshot(self.cycle, left[0]), snapshot(self.cycle, right[0]))

    def test_cold_restore_and_resume(self):
        state = initial_state(self.cycle)
        for tick in range(1, 17):
            state, _ = advance(self.cycle, state, tick=tick, expected_revision=state.revision)
        recovered_cycle, recovered_state = restore(snapshot(self.cycle, state))
        self.assertEqual((recovered_cycle, recovered_state), (self.cycle, state))
        self.assertEqual(advance(self.cycle, state, tick=17, expected_revision=state.revision),
                         advance(recovered_cycle, recovered_state, tick=17, expected_revision=recovered_state.revision))

    def test_offset_and_wrap(self):
        cycle = DaylightCycle(1, 1, 1, 1, 3)
        self.assertEqual([observation(cycle, tick=i)["phase"] for i in range(5)],
                         ["night", "dawn", "day", "dusk", "night"])
        self.assertEqual(observation(cycle, tick=1)["cycle_index"], 1)

    def test_maximum_tick(self):
        obs = observation(self.cycle, tick=MAX_TICK)
        self.assertEqual(obs["tick"], MAX_TICK)
        with self.assertRaises(ValueError):
            observation(self.cycle, tick=MAX_TICK + 1)
        state = DaylightState(MAX_TICK, obs["state_revision"])
        with self.assertRaises(ValueError):
            advance(self.cycle, state, tick=MAX_TICK + 1, expected_revision=state.revision)

    def test_invalid_durations_and_offset(self):
        for attrs in [dict(dawn_ticks=0), dict(dusk_ticks=-1),
                      dict(night_ticks=True), dict(day_ticks=1.1),
                      dict(dawn_ticks=1000001), dict(dawn_ticks=400000, day_ticks=400000, dusk_ticks=400000),
                      dict(offset_ticks=-1), dict(offset_ticks=240)]:
            with self.subTest(attrs=attrs), self.assertRaises(ValueError):
                DaylightCycle(**attrs)

    def test_invalid_logical_ticks(self):
        for tick in (True, False, -1, 1.0, "1", None, MAX_TICK + 1):
            with self.subTest(tick=tick), self.assertRaises(ValueError):
                observation(self.cycle, tick=tick)

    def test_invalid_step_and_stale_revisions(self):
        state = initial_state(self.cycle)
        for tick, revision in ((0, state.revision), (2, state.revision), (1, "bad"), (1, None)):
            with self.subTest(tick=tick, revision=revision), self.assertRaises(ValueError):
                advance(self.cycle, state, tick=tick, expected_revision=revision)
        other = DaylightCycle(1, 1, 1, 1)
        with self.assertRaises(ValueError):
            advance(other, state, tick=1, expected_revision=state.revision)

    def test_mutated_frozen_instances_revalidated(self):
        cycle = DaylightCycle()
        state = initial_state(cycle)
        object.__setattr__(cycle, "dawn_ticks", True)
        with self.assertRaises(ValueError):
            observation(cycle, tick=1)
        with self.assertRaises(ValueError):
            advance(cycle, state, tick=1, expected_revision=state.revision)
        cycle = DaylightCycle()
        state = initial_state(cycle)
        object.__setattr__(state, "tick", True)
        with self.assertRaises(ValueError):
            snapshot(cycle, state)

    def test_snapshot_tamper_rejected(self):
        state = initial_state(self.cycle)
        baseline = json.loads(snapshot(self.cycle, state))
        examples = [
            dict(baseline, schema="other"),
            dict(baseline, cycle_revision="0" * 64),
            dict(baseline, unknown=1),
            dict(baseline, cycle={**baseline["cycle"], "day_ticks": 2}),
            dict(baseline, state={**baseline["state"], "tick": 5}),
            dict(baseline, state={**baseline["state"], "revision": "garbage"}),
        ]
        for value in examples:
            with self.subTest(value=value), self.assertRaises(ValueError):
                restore(json.dumps(value, sort_keys=True, separators=(",", ":")))

    def test_snapshot_malformed_wire_rejected(self):
        good = snapshot(self.cycle, initial_state(self.cycle))
        malicious = [good + " ", " " + good, good.replace('"schema":', '"schema":"garbage","schema":'),
                     good.replace('"tick":0', '"tick":true'), good.replace('"tick":0', '"tick":0.0'),
                     good.replace('"tick":0', '"tick":NaN'), good.replace('"tick":0', '"tick":Infinity'),
                     good + " " * 4096, "[]", "null", "{}", '{"x":' * 90, "\ud800"]
        for raw in malicious:
            with self.subTest(raw=repr(raw)[:60]), self.assertRaises(ValueError):
                restore(raw)

    def test_no_system_clock_or_runtime_dependency(self):
        self.assertEqual(observation(self.cycle, tick=100000), observation(self.cycle, tick=100000))
        self.assertEqual(observation(self.cycle, tick=0)["provenance"], "synthetic-logical-clock-only")


if __name__ == "__main__":
    unittest.main()
