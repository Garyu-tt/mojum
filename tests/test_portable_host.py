"""Bounded foreground host tests against the current Aquarium ABI."""

from contextlib import closing
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium
from mojum.portable_host import (
    PortableHostError, main as host_main, run_paced, validate_plan,
)


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def read(self):
        return self.now

    def sleep(self, delay):
        self.waits.append(delay)
        self.now += delay


class SyntheticRunner:
    motor_mode = "developmental_fixture"
    sensory_mode = "toy_probe"

    def __init__(self, clock, simulated_work=0.0):
        self.clock = clock
        self.simulated_work = simulated_work
        self.starts = []
        self.store = self
        self.motor = object()
        self.sensory_bridge = object()

    def count(self):
        return 1 + len(self.starts)

    def step(self):
        self.starts.append(self.clock.now)
        self.clock.now += self.simulated_work


class PortableHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root / "private.sqlite3"
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))

    def command(self, *options):
        return [
            sys.executable, "-m", "mojum.portable_host",
            "--db", str(self.db), "--ticks", "4", "--interval-ms", "20",
            *options,
        ]

    def invoke(self, *options):
        return subprocess.run(self.command(*options), cwd=ROOT,
                              env=self.env, text=True, capture_output=True,
                              timeout=30)

    def open_aquarium(self, **options):
        return Aquarium(
            self.db, SEED, GRAPH,
            motor_mode="developmental_fixture", sensory_mode="toy_probe",
            temporal_mode="range_guided", **options,
        )

    def test_clock_paces_without_backlog_or_busy_loop(self):
        fake = Clock()
        runner = SyntheticRunner(fake)
        count = run_paced(runner, ticks=4, interval_ms=100,
                          stop_event=threading.Event(),
                          monotonic=fake.read, wait=fake.sleep)
        self.assertEqual(count, 4)
        self.assertEqual([round(x, 4) for x in runner.starts], [0, .1, .2, .3])
        self.assertEqual([round(x, 4) for x in fake.waits], [.1, .1, .1])

        late = Clock()
        slow = SyntheticRunner(late, simulated_work=.24)
        run_paced(slow, ticks=3, interval_ms=100,
                  stop_event=threading.Event(),
                  monotonic=late.read, wait=late.sleep)
        self.assertEqual([round(x, 4) for x in slow.starts], [0, .34, .68])
        self.assertEqual([round(x, 4) for x in late.waits], [.1, .1])

    def test_early_wait_return_never_commits_before_deadline(self):
        fake = Clock()
        runner = SyntheticRunner(fake)
        waits = []

        def wake_early(remaining):
            waits.append(round(remaining, 4))
            fake.now += .025 if len(waits) == 1 else remaining

        committed = run_paced(
            runner, ticks=2, interval_ms=100,
            stop_event=threading.Event(),
            monotonic=fake.read, wait=wake_early,
        )
        self.assertEqual(committed, 2)
        self.assertEqual([round(t, 4) for t in runner.starts], [0, .1])
        self.assertEqual(waits, [.1, .075])

    def test_stop_during_wait_does_not_append_an_extra_tick(self):
        fake = Clock()
        runner = SyntheticRunner(fake)
        stop = threading.Event()

        def interrupt_wait(_remaining):
            fake.now += .025
            stop.set()

        committed = run_paced(
            runner, ticks=3, interval_ms=100,
            stop_event=stop, monotonic=fake.read, wait=interrupt_wait,
        )
        self.assertEqual(committed, 1)
        self.assertEqual([round(t, 4) for t in runner.starts], [0])

    def test_cancel_before_first_tick_does_not_write_frame(self):
        event = threading.Event()
        event.set()
        with self.open_aquarium() as aquarium:
            count = aquarium.store.count()
            self.assertEqual(run_paced(
                aquarium, ticks=20, interval_ms=1000,
                stop_event=event,
            ), 0)
            self.assertEqual(aquarium.store.count(), count)
            self.assertEqual(aquarium.tick, 0)

    def test_budget_rejection_does_not_append_partial_history(self):
        with self.open_aquarium() as aquarium:
            before = aquarium.checkpoint()
            with patch("mojum.portable_host.MAX_REPLAY_EVENTS", 3):
                with self.assertRaisesRegex(PortableHostError, "10000-event"):
                    run_paced(aquarium, ticks=3, interval_ms=20,
                              stop_event=threading.Event())
            self.assertEqual(aquarium.checkpoint(), before)
            self.assertEqual(aquarium.store.count(), 1)

    def test_strict_plan_typing_and_resource_limits(self):
        for ticks, interval in (
            (0, 100), (1001, 100), (True, 100), ("100", 100),
            (1, 0), (1, 19), (1, 3600001), (1, float("nan")),
            (1, True), (1, "20"),
        ):
            with self.subTest(ticks=ticks, interval=interval):
                with self.assertRaises(PortableHostError):
                    validate_plan(ticks, interval)
        self.assertEqual(validate_plan(1, 20), .02)

    def test_cli_invalid_plan_does_not_birth_an_organism(self):
        for opts in (("--ticks", "0"), ("--ticks", "1001"),
                     ("--interval-ms", "0"), ("--interval-ms", "3600001")):
            with self.subTest(opts=opts):
                run = self.invoke(*opts)
                self.assertNotEqual(run.returncode, 0)
                self.assertFalse(self.db.exists(), run.stdout + run.stderr)

    def test_real_cli_restart_replays_same_journal_and_keeps_private_stdout(self):
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        one = json.loads(first.stdout)
        self.assertEqual(one["start_tick"], 0)
        self.assertEqual(one["end_tick"], 4)
        self.assertEqual(one["committed_ticks"], 4)
        self.assertEqual(one["journal_events"], 5)
        self.assertEqual(one["stop_reason"], "completed")
        self.assertNotIn("measured_contact", first.stdout)
        self.assertNotIn("genome", first.stdout)
        self.assertNotIn("neural_state", first.stdout)
        self.assertEqual(one["classification"],
                         "PRIVATE_SYNTHETIC_FOREGROUND_NOT_FULL_BRAIN")
        with self.open_aquarium() as reopened:
            self.assertEqual(reopened.tick, 4)
            self.assertGreaterEqual(reopened.contact_model.snapshot()["updates"], 1)
            head = reopened.checkpoint()["journal_head"]
        second = self.invoke()
        self.assertEqual(second.returncode, 0, second.stderr)
        two = json.loads(second.stdout)
        self.assertEqual(two["start_tick"], 4)
        self.assertEqual(two["end_tick"], 8)
        self.assertEqual(two["journal_events"], 9)
        self.assertNotEqual(two["journal_head"], head)
        with self.open_aquarium() as reopened:
            self.assertEqual(reopened.tick, 8)
            self.assertEqual(reopened.checkpoint()["journal_head"],
                             two["journal_head"])

    def test_wrong_mode_or_head_refuses_extra_events(self):
        self.assertEqual(self.invoke().returncode, 0)
        with self.open_aquarium() as q:
            initial_head = q.checkpoint()["journal_head"]
            initial_events = q.store.count()
        for opts in (
            ("--temporal-mode", "none"),
            ("--expected-head", "0" * 64),
        ):
            with self.subTest(opts=opts):
                process = self.invoke(*opts)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(process.stdout, "")
                with self.open_aquarium() as q:
                    self.assertEqual(q.store.count(), initial_events)
                    self.assertEqual(q.checkpoint()["journal_head"], initial_head)

    def test_controller_mode_replays_and_cross_mode_reopen_fails_closed(self):
        first = self.invoke("--brain-mode", "developmental_controller")
        self.assertEqual(first.returncode, 0, first.stderr)
        result = json.loads(first.stdout)
        self.assertEqual(result["brain_mode"], "developmental_controller")
        with self.open_aquarium(brain_mode="developmental_controller") as aquarium:
            self.assertEqual(aquarium.tick, 4)
            items = aquarium.store.dataset(include_nonpublic=True, limit=10)["projection"]["items"]
            self.assertEqual(items[-1]["event"]["payload"]["brain"]["status"],
                             "SYNTHETIC_DEVELOPMENTAL_CONTROLLER")
            head = aquarium.checkpoint()["journal_head"]
            count = aquarium.store.count()
        wrong = self.invoke()
        self.assertNotEqual(wrong.returncode, 0)
        self.assertEqual(wrong.stdout, "")
        with self.open_aquarium(brain_mode="developmental_controller") as aquarium:
            self.assertEqual(aquarium.store.count(), count)
            self.assertEqual(aquarium.checkpoint()["journal_head"], head)
        second = self.invoke("--brain-mode", "developmental_controller")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)["start_tick"], 4)

    @unittest.skipUnless(os.name == "posix" and hasattr(signal, "SIGTERM"),
                         "POSIX process signal required")
    def test_sigterm_stops_between_commits_and_reopens_exactly(self):
        command = [
            sys.executable, "-m", "mojum.portable_host", "--db", str(self.db),
            "--ticks", "500", "--interval-ms", "1000",
        ]
        process = subprocess.Popen(command, cwd=ROOT, env=self.env,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 8
            found = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                if self.db.exists():
                    try:
                        uri = self.db.resolve().as_uri() + "?mode=ro"
                        with closing(sqlite3.connect(uri, uri=True)) as db:
                            if db.execute("SELECT COUNT(*) FROM events").fetchone()[0] >= 2:
                                found = True
                                break
                    except sqlite3.DatabaseError:
                        pass
                time.sleep(.02)
            self.assertTrue(found, "portable host failed to commit first tick")
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 0, stderr)
            result = json.loads(stdout)
            self.assertEqual(result["stop_reason"], "signal")
            self.assertGreaterEqual(result["committed_ticks"], 1)
            self.assertLess(result["committed_ticks"], 500)
            with self.open_aquarium() as q:
                self.assertEqual(q.tick, result["end_tick"])
                self.assertEqual(q.store.count(), result["journal_events"])
                self.assertEqual(q.checkpoint()["journal_head"],
                                 result["journal_head"])
            again = self.invoke()
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(json.loads(again.stdout)["start_tick"],
                             result["end_tick"])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
