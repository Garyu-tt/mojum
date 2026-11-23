"""Offline deterministic cloth LOD admission, replay and fail-closed tests."""

import copy
import unittest

from mojum.cloth_lod import (
    ClothLODConfig, ClothLODError, MeshCost, advance, restore_state, start,
)


class ClothLODTests(unittest.TestCase):
    def test_default_disabled(self):
        c = ClothLODConfig()
        state = start(c)
        for tick in range(60):
            d, state = advance(c, state, tick)
            self.assertFalse(d.simulate)
            self.assertEqual(d.effective, "off")
        self.assertEqual(state.next_tick, 60)

    def test_cadence_and_substeps(self):
        for mode, cadence in (("low", 4), ("medium", 2), ("high", 1)):
            with self.subTest(mode=mode):
                c = ClothLODConfig(requested=mode)
                state = start(c)
                for tick in range(20):
                    cost = 100 if state.pending_measurement else None
                    d, state = advance(c, state, tick, cost)
                    self.assertEqual(d.simulate, tick % cadence == 0)
                    self.assertEqual(d.substeps, 2 if d.simulate else 0)

    def test_all_resource_caps_gate_admission(self):
        c = ClothLODConfig(requested="high", max_vertices=300,
                           max_constraints=1000, max_bytes=300000)
        self.assertEqual(start(c).effective, "low")
        self.assertEqual(start(ClothLODConfig(requested="high", max_bytes=1)).effective, "off")

    def test_overrun_degrades_and_safe_measured_steps_recover(self):
        c = ClothLODConfig(requested="high", cpu_budget_us=100)
        state = start(c)
        _, state = advance(c, state, 0)
        d, state = advance(c, state, 1, 101)
        self.assertEqual((d.effective, d.reason), ("medium", "cpu_over_budget"))
        recovered = False
        for tick in range(2, 20):
            d, state = advance(c, state, tick, 80 if state.pending_measurement else None)
            if d.reason == "cpu_recovery":
                self.assertEqual(d.effective, "high")
                recovered = True
                break
        self.assertTrue(recovered)

    def test_low_overrun_latches_off(self):
        c = ClothLODConfig(requested="low", cpu_budget_us=100)
        _, state = advance(c, start(c), 0)
        d, state = advance(c, state, 1, 101)
        self.assertTrue(state.fault_latched)
        self.assertFalse(d.simulate)
        for tick in range(2, 15):
            _, state = advance(c, state, tick)
        self.assertEqual(state.effective, "off")

    def test_missing_cpu_measurement_fails_closed(self):
        c = ClothLODConfig(requested="medium")
        _, state = advance(c, start(c), 0)
        d, state = advance(c, state, 1)
        self.assertEqual(d.reason, "measurement_missing")
        self.assertTrue(state.fault_latched)
        self.assertFalse(d.simulate)

    def test_cold_replay_matches_continuous_trace(self):
        c = ClothLODConfig(requested="medium")
        def run(restart=None):
            state = start(c)
            decisions = []
            for tick in range(10050):
                if tick == restart:
                    state = restore_state(copy.deepcopy(state.to_snapshot()), c)
                measured = (200 if tick in (1, 10) else 100) if state.pending_measurement else None
                d, state = advance(c, state, tick, measured)
                decisions.append(d)
            return decisions, state.to_snapshot()
        self.assertEqual(run(), run(8))
        self.assertEqual(run(), run(9999))

    def test_rejects_policy_reinterpretation_and_forged_state(self):
        c = ClothLODConfig(requested="low")
        snap = start(c).to_snapshot()
        with self.assertRaisesRegex(ClothLODError, "policy mismatch"):
            restore_state(snap, ClothLODConfig(requested="medium"))
        for changed in ({"unexpected": 1}, {"effective": "high"},
                        {"next_tick": True}, {"fault_latched": 1},
                        {"pending_measurement": True},
                        {"schema": "mojum.cloth.lod-state/2"}):
            with self.subTest(changed=changed), self.assertRaises(ClothLODError):
                restore_state({**snap, **changed}, c)

    def test_rejects_nonsequential_and_nonfinite_work_inputs(self):
        c = ClothLODConfig(requested="high")
        original = start(c)
        for bad in (True, -1, 1.0, 2**63):
            with self.subTest(bad=bad), self.assertRaises(ClothLODError):
                advance(c, original, bad)
        with self.assertRaises(ClothLODError):
            advance(c, original, 1)
        with self.assertRaises(ClothLODError):
            advance(c, original, 0, 10)
        with self.assertRaises(ClothLODError):
            ClothLODConfig(low=MeshCost(4096, 100, 1))
        self.assertEqual(start(c), original)


    def test_restored_snapshot_tags_reject_hostile_equality(self):
        """No arbitrary Python __eq__/__ne__ can bypass schema or policy CAS."""
        config = ClothLODConfig(requested="medium")
        valid = start(config).to_snapshot()

        class PretendsEqual:
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        class ExplodesOnCompare:
            def __eq__(self, other):
                raise AssertionError("untrusted equality executed")

            def __ne__(self, other):
                raise AssertionError("untrusted inequality executed")

        class StringSubclass(str):
            pass

        for key in ("schema", "policy_sha256"):
            for hostile in (PretendsEqual(), ExplodesOnCompare(),
                            StringSubclass(valid[key])):
                with self.subTest(key=key, hostile=type(hostile).__name__):
                    with self.assertRaises(ClothLODError):
                        restore_state({**valid, key: hostile}, config)

        # A forged policy string must never authorize replay under another
        # config, even when the old effective mode fits the new resource caps.
        from_low = start(ClothLODConfig(requested="low")).to_snapshot()
        from_low["policy_sha256"] = PretendsEqual()
        with self.assertRaises(ClothLODError):
            restore_state(from_low, config)

        # Key spelling equality is not sufficient for a strict JSON field map.
        alias_keys = {StringSubclass(k): v for k, v in valid.items()}
        with self.assertRaises(ClothLODError):
            restore_state(alias_keys, config)


if __name__ == "__main__":
    unittest.main()
