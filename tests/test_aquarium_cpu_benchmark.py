"""Hermetic tests for the disposable Aquarium benchmark harness.

All subprocesses are fakes here. The real Aquarium CLI is exercised only by
opt-in invocation / the dedicated exact-head CI smoke below.
"""
from __future__ import annotations

import importlib.util
import itertools
import json
from pathlib import Path
import subprocess
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "aquarium_cpu_benchmark.py"
spec = importlib.util.spec_from_file_location("aquarium_cpu_benchmark", TOOL)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def checkpoint(tick, count, head="a" * 64):
    return json.dumps({
        "tick": tick, "stored_events": count, "journal_head": head,
        "status": "SYNTHETIC_PLACEHOLDER_NOT_NEURAL_OR_MUJOCO",
    })


class FakeRunner:
    def __init__(self, *, ticks=3, bad_outputs=None):
        self.calls = []
        self.ticks = ticks
        self.bad_outputs = bad_outputs

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        n = len(self.calls)
        head = chr(ord("a") + n - 1) * 64
        text = checkpoint(
            self.ticks if n % 2 else 2 * self.ticks,
            self.ticks + 1 if n % 2 else 2 * self.ticks + 1,
            head,
        )
        if self.bad_outputs is not None:
            text = self.bad_outputs[n - 1]
        return subprocess.CompletedProcess(command, 0, stdout=text, stderr="")


class BenchmarkTests(unittest.TestCase):
    def test_disposable_cold_restart_and_exact_head_handoff(self):
        fake = FakeRunner(ticks=3)
        now = itertools.count(0, 100).__next__
        report = bench.benchmark(
            3, 2, 15, runner=fake, clock=now, cpu_clock=lambda: None,
        )
        self.assertEqual(report["schema"], bench.SCHEMA)
        self.assertEqual(report["fixture"], "PUBLIC_SYNTHETIC_DISPOSABLE")
        self.assertEqual(len(fake.calls), 4)
        self.assertEqual(report["median_cold_wall_ns"], 100)
        self.assertEqual(report["median_restart_wall_ns"], 100)
        self.assertIsNone(report["median_restart_child_cpu_ns"])
        for i in (0, 2):
            fresh, opts = fake.calls[i]
            restart, restart_opts = fake.calls[i + 1]
            self.assertEqual(fresh[:3], [sys.executable, "-m", "mojum.aquarium"])
            self.assertNotIn("--expected-head", fresh)
            self.assertEqual(restart[:-2], fresh)
            self.assertEqual(restart[-2:], ["--expected-head", chr(ord("a") + i) * 64])
            self.assertEqual(opts["stdin"], subprocess.DEVNULL)
            self.assertTrue(opts["capture_output"])
            self.assertFalse(opts["check"])
            self.assertEqual(restart_opts["timeout"], 15)
            self.assertFalse(Path(fresh[fresh.index("--db") + 1]).exists())
        self.assertNotEqual(
            fake.calls[0][0][fake.calls[0][0].index("--db") + 1],
            fake.calls[2][0][fake.calls[2][0].index("--db") + 1],
        )
        report_json = json.dumps(report)
        self.assertNotIn("sqlite3", report_json)
        self.assertNotIn("aaaaaaaa", report_json)

    def test_child_cpu_is_measured_separately_and_medians_are_bounded(self):
        fake = FakeRunner(ticks=1)
        clock = iter([10, 110, 200, 400]).__next__
        cpu = iter([100, 130, 130, 200]).__next__
        report = bench.benchmark(1, 1, 1, runner=fake, clock=clock, cpu_clock=cpu)
        self.assertEqual(report["measurements"][0]["cold"],
                         {"wall_ns": 100, "child_cpu_ns": 30})
        self.assertEqual(report["measurements"][0]["restart"],
                         {"wall_ns": 200, "child_cpu_ns": 70})

    def test_rejects_invalid_limits_before_spawning(self):
        for args in ((True, 1, 1), (0, 1, 1), (1001, 1, 1),
                     (1, False, 1), (1, 13, 1), (1, 1, 0),
                     (1, 1, 121), (1.0, 1, 1)):
            with self.subTest(args=args):
                with self.assertRaises(bench.BenchmarkError):
                    bench.benchmark(*args, runner=lambda *_a, **_k: self.fail("spawned"))

    def test_checkpoint_rejects_non_contiguous_events_and_ticks(self):
        for wrong in (checkpoint(2, 4), checkpoint(3, 6),
                      checkpoint(True, 4), checkpoint(3, False),
                      checkpoint(3, 4, "not-sha"),
                      checkpoint(3, 4, "A" * 64)):
            with self.subTest(payload=wrong):
                with self.assertRaises(bench.BenchmarkError):
                    bench._checkpoint(wrong, expected_tick=3, expected_events=4)

    def test_checkpoint_rejects_duplicate_keys_nonfinite_and_non_objects(self):
        for bad in ('{"tick":3,"tick":3,"stored_events":4,"journal_head":"' + "a"*64 + '"}',
                    '{"tick":NaN}', '[]', 'not JSON', '{"tick":Infinity}',
                    '{"nested":{"x":1,"x":2}}'):
            with self.subTest(payload=bad[:35]):
                with self.assertRaises(bench.BenchmarkError):
                    bench._checkpoint(bad, expected_tick=3, expected_events=4)

    def test_checkpoint_output_byte_budget(self):
        oversized = checkpoint(3, 4) + (" " * bench.MAX_OUTPUT_BYTES)
        with self.assertRaisesRegex(bench.BenchmarkError, "limit"):
            bench._checkpoint(oversized, expected_tick=3, expected_events=4)

    def test_aborts_before_restart_on_bad_first_phase(self):
        fake = FakeRunner(ticks=1, bad_outputs=["{}", checkpoint(2, 3, "b"*64)])
        with self.assertRaises(bench.BenchmarkError):
            bench.benchmark(1, 1, 1, runner=fake)
        self.assertEqual(len(fake.calls), 1)

    def test_aborts_if_restart_head_did_not_advance(self):
        fake = FakeRunner(ticks=1, bad_outputs=[
            checkpoint(1, 2), checkpoint(2, 3),
        ])
        with self.assertRaisesRegex(bench.BenchmarkError, "did not advance"):
            bench.benchmark(1, 1, 1, runner=fake)

    def test_nonzero_child_stderr_not_leaked(self):
        def failed(command, **kwargs):
            return subprocess.CompletedProcess(command, 2, stdout="", stderr="SECRET-TOKEN")
        with self.assertRaises(bench.BenchmarkError) as caught:
            bench.benchmark(1, 1, 1, runner=failed)
        self.assertNotIn("SECRET-TOKEN", str(caught.exception))

    def test_timeout_and_oserror_are_benchmark_errors(self):
        def timeout(command, **kwargs):
            raise subprocess.TimeoutExpired(command, timeout=1)
        def fail(command, **kwargs):
            raise OSError("sensitive local path")
        for runner in (timeout, fail):
            with self.subTest(runner=runner.__name__):
                with self.assertRaises(bench.BenchmarkError):
                    bench.benchmark(1, 1, 1, runner=runner)

    def test_rejects_non_monotonic_clock_and_child_cpu(self):
        for wall, cpu in ((iter([20, 19]).__next__, lambda: None),
                          (iter([10, 20]).__next__,
                           iter([50, 40]).__next__)):
            with self.assertRaises(bench.BenchmarkError):
                bench.benchmark(1, 1, 1, runner=FakeRunner(ticks=1),
                                clock=wall, cpu_clock=cpu)


    def test_real_disposable_aquarium_cold_restart(self):
        """Run two true Python/SQLite processes against a disposable fixture."""
        import os
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(TOOL.parents[1] / "src")

        def real_runner(command, **kwargs):
            return subprocess.run(command, env=environment, **kwargs)

        report = bench.benchmark(1, 1, 30, runner=real_runner)
        self.assertEqual(report["repetitions"], 1)
        self.assertEqual(report["ticks_per_phase"], 1)
        self.assertGreater(report["median_cold_wall_ns"], 0)
        self.assertGreater(report["median_restart_wall_ns"], 0)


if __name__ == "__main__":
    unittest.main()
