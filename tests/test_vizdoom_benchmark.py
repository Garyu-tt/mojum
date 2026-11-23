"""stdlib contract tests; no ViZDoom install or network needed."""
import math
import unittest

from mojum import vizdoom_benchmark as mod


class ViZDoomBenchmarkTests(unittest.TestCase):
    def test_two_policies_and_replay_without_external_import(self):
        r = mod.benchmark(seed=11, steps=12)
        self.assertEqual(r["contract"], "mojum.benchmark.vizdoom-vision/1")
        self.assertTrue(r["replay_equal"])
        self.assertFalse(r["learning"])
        self.assertEqual(set(r["comparison"]), {"frozen", "vision"})
        self.assertNotIn("trace", r["comparison"]["frozen"])

    def test_heldout_fake_seeds_and_nonlearning_control(self):
        # These are only synthetic regression seeds, not real-engine acceptance.
        for seed in (0, 7, 42, 2026, 2**32 - 1):
            with self.subTest(seed=seed):
                output = mod.benchmark(seed=seed, steps=32)
                self.assertTrue(output["replay_equal"])
                self.assertGreater(output["comparison"]["vision"]["total_reward"],
                                   output["comparison"]["frozen"]["total_reward"])
                self.assertLess(output["comparison"]["vision"]["steps"], 32)

    def test_identical_trace_and_closed_game(self):
        made = []
        def factory(seed, steps):
            game = mod.SyntheticVisualGame(seed, steps)
            made.append(game)
            return game
        a = mod.run_episode(factory, seed=4, steps=7, policy="vision")
        b = mod.run_episode(factory, seed=4, steps=7, policy="vision")
        self.assertEqual(a, b)
        self.assertTrue(all(game.closed for game in made))
        self.assertEqual(len(a["trace_sha256"]), 64)

    def test_frozen_does_not_inspect_privileged_state(self):
        class State:
            @property
            def game_variables(self):
                raise AssertionError("privileged engine state accessed")
            @property
            def screen_buffer(self):
                return bytes([200]) * mod.FRAME_BYTES
        class Game:
            def new_episode(self): pass
            def is_episode_finished(self): return False
            def get_state(self): return State()
            def make_action(self, action, tics):
                self.action = action
                return 0
            def close(self): self.closed = True
        game = Game()
        result = mod.run_episode(lambda *args: game, seed=0, steps=1, policy="frozen")
        self.assertEqual(result["steps"], 1)
        self.assertEqual(game.action, [1, 0, 0])
        self.assertTrue(game.closed)

    def test_fail_closed_limits_and_policy(self):
        for seed, steps in [(-1, 1), (2**32, 1), (False, 1), (3, 0),
                            (3, mod.MAX_STEPS + 1), (3, True)]:
            with self.subTest(seed=seed, steps=steps), self.assertRaises(ValueError):
                mod.benchmark(seed=seed, steps=steps)
        with self.assertRaises(ValueError):
            mod.run_episode(mod.SyntheticVisualGame, seed=0, steps=1, policy="learn")
        with self.assertRaises(ValueError):
            mod.benchmark(engine="network")

    def test_malformed_frames_close_engine(self):
        for screen in (None, b"", b"x" * (mod.FRAME_BYTES + 1)):
            class State: pass
            class Broken(mod.SyntheticVisualGame):
                def get_state(self):
                    state = State()
                    state.screen_buffer = screen
                    return state
            game = Broken(0, 2)
            with self.subTest(screen_size=None if screen is None else len(screen)):
                with self.assertRaises(ValueError):
                    mod.run_episode(lambda *args: game, seed=0, steps=2, policy="vision")
                self.assertTrue(game.closed)

    def test_nonfinite_reward_close_engine(self):
        class Broken(mod.SyntheticVisualGame):
            def make_action(self, action, tics): return math.nan
        game = Broken(0, 1)
        with self.assertRaises(ValueError):
            mod.run_episode(lambda *args: game, seed=0, steps=1, policy="frozen")
        self.assertTrue(game.closed)

    def test_policy_only_observes_pixels(self):
        dark = bytes([10]) * mod.FRAME_BYTES
        bright = bytes([200]) * mod.FRAME_BYTES
        h1, low = mod._vision(dark)
        h2, high = mod._vision(bright)
        self.assertNotEqual(h1, h2)
        self.assertNotEqual(low, high)
        self.assertEqual(mod._choose("vision", high), "forward")
        self.assertEqual(mod._choose("vision", low), "left")

    def test_no_file_io_or_authority_state(self):
        r = mod.benchmark(seed=10, steps=3)
        self.assertFalse(r["organism_access"])
        self.assertEqual(r["engine"], "fixture")


if __name__ == "__main__":
    unittest.main()
