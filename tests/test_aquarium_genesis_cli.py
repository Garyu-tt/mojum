"""Real-journal acceptance for the installed single-acquisition command."""

from contextlib import closing, redirect_stderr, redirect_stdout
import copy
from importlib.resources import files
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import mojum
from mojum.aquarium import Aquarium
from mojum.aquarium_genesis import MAX_SEED_BYTES, main
from mojum.genesis import ACQUISITION_SCHEMA
from mojum.room_draft_runtime import load_runtime_room
from mojum.sensory_play import ToyScene


DATA = files("mojum").joinpath("data")
SEED = json.loads(DATA.joinpath("genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads(DATA.joinpath("first_loop.json").read_text(encoding="utf-8"))


class AquariumGenesisCliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.db = self.directory / "organism.sqlite3"

    def aquarium(self, **options):
        return Aquarium(self.db, SEED, GRAPH, **options)

    def boot(self, **options):
        with self.aquarium(**options) as aquarium:
            aquarium.step()
            return aquarium.checkpoint()

    def command(self, command="status", *options, head=None):
        args = [command, "--db", str(self.db), *options]
        if head is not None:
            args.extend(("--expected-head", head))
        if command == "acquire":
            args.extend(("--event-id", "synthetic-gain", "--gene", "heartbeat_bpm",
                         "--delta", "15", "--source", "private synthetic source",
                         "--evidence", "private synthetic evidence"))
        return args

    def invoke(self, args):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(args)
        self.assertEqual(json.loads(output.getvalue()), result)
        self.assertTrue(output.getvalue().isascii())
        return result

    def reject(self, args):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            with self.assertRaises(SystemExit) as raised:
                main(args)
        self.assertEqual(raised.exception.code, 2, errors.getvalue())
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("Traceback", errors.getvalue())
        return errors.getvalue()

    def subprocess_command(self, module, *args):
        command = [sys.executable]
        if sys.flags.isolated:
            command.append("-I")
        command.extend(("-m", module, *args))
        env = dict(os.environ, PYTHONPATH=str(Path(mojum.__file__).resolve().parents[1]))
        return subprocess.run(command, cwd=self.directory, env=env,
                              capture_output=True, text=True, timeout=15)

    def test_real_cli_boot_acquire_resume_and_cold_replay(self):
        boot = self.subprocess_command("mojum.aquarium", "--db", str(self.db), "--ticks", "2")
        self.assertEqual(boot.returncode, 0, boot.stderr)
        before = json.loads(boot.stdout)
        run = self.subprocess_command(
            "mojum.aquarium_genesis", *self.command("acquire", head=before["journal_head"]))
        self.assertEqual(run.returncode, 0, run.stderr)
        acquired = json.loads(run.stdout)
        self.assertEqual((acquired["tick"], acquired["stored_events"], acquired["acquisitions"]), (2, 4, 1))
        self.assertEqual(acquired["previous_journal_head"], before["journal_head"])
        self.assertNotEqual(acquired["genome_revision"], before["genome_revision"])
        for secret in ("private synthetic", "heartbeat_bpm", "phenotype", "synthetic-gain"):
            self.assertNotIn(secret, run.stdout)
        resumed = self.subprocess_command(
            "mojum.aquarium", "--db", str(self.db), "--ticks", "1",
            "--expected-head", acquired["journal_head"])
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        with self.aquarium() as aquarium:
            self.assertEqual((aquarium.tick, aquarium.stored_events), (3, 5))
            self.assertEqual(aquarium.genome.phenotype(2)["parameters"]["heartbeat_bpm"], 75)
            event = aquarium.genome.snapshot()["events"][0]
            self.assertEqual(event["schema"], ACQUISITION_SCHEMA)
            self.assertEqual((event["valid_from_tick"], event["recorded_at_ns"], event["observed_at_ns"]),
                             (2, 20_000_000, 20_000_000))
            self.assertEqual(aquarium.checkpoint()["journal_head"], json.loads(resumed.stdout)["journal_head"])

    def test_acquisition_preserves_runtime_until_its_effective_tick(self):
        before = self.boot(motor_mode="developmental_fixture", sensory_mode="toy_probe",
                           temporal_mode="range_guided", brain_mode="developmental_controller")
        profile = ["--motor-mode", "developmental_fixture", "--sensory-mode", "toy_probe",
                   "--temporal-mode", "range_guided", "--brain-mode", "developmental_controller"]
        result = self.invoke(self.command("acquire", *profile, "--valid-from-tick", "3",
                                          "--observed-at-ns", "0", head=before["journal_head"]))
        self.assertEqual((result["effective_acquisitions"], result["pending_acquisitions"]), (0, 1))
        with self.aquarium(motor_mode="developmental_fixture", sensory_mode="toy_probe",
                           temporal_mode="range_guided", brain_mode="developmental_controller") as aquarium:
            after = aquarium.checkpoint()
            for key in before:
                if key not in ("journal_head", "genome_revision"):
                    self.assertEqual(after[key], before[key], key)
            self.assertEqual(aquarium.step()["phenotype"]["parameters"]["heartbeat_bpm"], 60)
            self.assertEqual(aquarium.step()["phenotype"]["parameters"]["heartbeat_bpm"], 60)
            self.assertEqual(aquarium.step()["phenotype"]["parameters"]["heartbeat_bpm"], 75)
        status = self.invoke(self.command("status", *profile))
        self.assertEqual((status["tick"], status["effective_acquisitions"], status["pending_acquisitions"]), (4, 1, 0))

    def test_status_uses_read_only_replay_and_leaves_closed_file_unchanged(self):
        before = self.boot()
        original = self.db.read_bytes(), self.db.stat().st_mode
        with patch("mojum.aquarium_genesis.Aquarium", wraps=Aquarium) as constructor:
            report = self.invoke(self.command(head=before["journal_head"]))
        self.assertEqual(constructor.call_count, 1)
        self.assertIs(constructor.call_args.kwargs["read_only"], True)
        self.assertEqual(report["journal_head"], before["journal_head"])
        self.assertEqual((self.db.read_bytes(), self.db.stat().st_mode), original)
        # SQLite may create WAL coordination files even for mode=ro. No
        # application event, main-file content or permission change is made.
        with self.aquarium(read_only=True) as aquarium:
            self.assertEqual(aquarium.stored_events, 2)

    def test_missing_empty_and_foreign_sources_are_never_initialized(self):
        for kind in ("missing", "empty", "foreign"):
            with self.subTest(kind=kind):
                self.db = self.directory / (kind + ".sqlite3")
                if kind == "empty":
                    self.db.touch()
                elif kind == "foreign":
                    with closing(sqlite3.connect(self.db)) as db:
                        db.execute("CREATE TABLE user_notes (note TEXT)")
                        db.commit()
                before = self.db.read_bytes() if self.db.exists() else None
                self.reject(self.command())
                self.reject(self.command("acquire", head="0" * 64))
                self.assertEqual(self.db.read_bytes() if self.db.exists() else None, before)
                self.assertFalse(Path(str(self.db) + "-wal").exists())
                self.assertFalse(Path(str(self.db) + "-shm").exists())

    def test_anchor_is_required_and_stale_or_malformed_heads_never_open_writable(self):
        before = self.boot()
        original = self.db.read_bytes()
        for head in (None, "bad", "A" * 64, "0" * 64):
            with self.subTest(head=head), patch("mojum.aquarium_genesis.Aquarium", wraps=Aquarium) as constructor:
                self.reject(self.command("acquire", head=head))
                self.assertTrue(all(call.kwargs.get("read_only") is True for call in constructor.call_args_list))
                self.assertEqual(self.db.read_bytes(), original)
        self.assertEqual(self.invoke(self.command())["journal_head"], before["journal_head"])

    def test_invalid_acquisitions_fail_during_read_only_preflight(self):
        before = self.boot()
        original = self.db.read_bytes()
        changes = (("--delta", "2000"), ("--delta", "true"), ("--delta", "15.0"),
                   ("--valid-from-tick", "0"), ("--valid-from-tick", str(2**63)),
                   ("--observed-at-ns", "10000001"), ("--observed-at-ns", "-1"),
                   ("--event-id", ""), ("--event-id", "x" * 129),
                   ("--source", ""), ("--evidence", "x" * 513), ("--gene", "unknown"))
        for flag, value in changes:
            with self.subTest(flag=flag, value=value[:25]), patch("mojum.aquarium_genesis.Aquarium", wraps=Aquarium) as constructor:
                args = self.command("acquire", head=before["journal_head"])
                if flag in args:
                    args[args.index(flag) + 1] = value
                else:
                    args.extend((flag, value))
                self.reject(args)
                self.assertTrue(all(call.kwargs.get("read_only") is True for call in constructor.call_args_list))
                self.assertEqual(self.db.read_bytes(), original)

    def test_duplicate_and_future_prefix_overflow_do_not_reopen_writable(self):
        before = self.boot()
        first = self.invoke(self.command("acquire", "--valid-from-tick", "3", head=before["journal_head"]))
        original = self.db.read_bytes()
        duplicate = self.command("acquire", head=first["journal_head"])
        overflow = self.command("acquire", head=first["journal_head"])
        overflow[overflow.index("--event-id") + 1] = "another-event"
        # Current 60 + 110 = 170 is valid, but the previously scheduled +15
        # would exceed 180 at tick 3. Canonical all-prefix validation must run.
        overflow[overflow.index("--delta") + 1] = "110"
        for args in (duplicate, overflow):
            with patch("mojum.aquarium_genesis.Aquarium", wraps=Aquarium) as constructor:
                self.reject(args)
                self.assertEqual(constructor.call_count, 1)
                self.assertIs(constructor.call_args.kwargs["read_only"], True)
                self.assertEqual(self.db.read_bytes(), original)

    def test_exhausted_journal_and_local_cell_writes_are_rejected(self):
        before = self.boot()
        with patch("mojum.aquarium.MAX_REPLAY_EVENTS", 2), \
             patch("mojum.aquarium_genesis.Aquarium", wraps=Aquarium) as constructor:
            self.assertIn("event budget", self.reject(self.command("acquire", head=before["journal_head"])))
            self.assertEqual(constructor.call_count, 1)
            self.assertIs(constructor.call_args.kwargs["read_only"], True)
        self.db = self.directory / "v4.sqlite3"
        before = self.boot(motor_mode="local_cell_v4")
        with patch("mojum.aquarium_genesis.Aquarium", wraps=Aquarium) as constructor:
            self.reject(self.command("acquire", "--motor-mode", "local_cell_v4", head=before["journal_head"]))
            constructor.assert_not_called()
        status = self.invoke(self.command("status", "--motor-mode", "local_cell_v4"))
        self.assertEqual(status["journal_head"], before["journal_head"])

    def test_writer_between_preflight_and_reopen_is_not_rebased(self):
        before = self.boot()
        opens = []

        def race(*args, **kwargs):
            opens.append(kwargs.get("read_only", False))
            if not kwargs.get("read_only"):
                with self.aquarium() as competitor:
                    competitor.step()
            return Aquarium(*args, **kwargs)

        with patch("mojum.aquarium_genesis.Aquarium", side_effect=race):
            self.reject(self.command("acquire", head=before["journal_head"]))
        self.assertEqual(opens, [True, False])
        with self.aquarium() as aquarium:
            self.assertEqual((aquarium.tick, aquarium.stored_events), (2, 3))
            self.assertEqual(aquarium.genome.snapshot()["events"], [])

    def test_writer_after_writable_reopen_is_stopped_by_acquire_cas(self):
        before = self.boot()
        acquire = Aquarium.acquire

        def race(aquarium, event):
            with self.aquarium() as competitor:
                competitor.step()
            return acquire(aquarium, event)

        with patch.object(Aquarium, "acquire", race):
            errors = self.reject(self.command("acquire", head=before["journal_head"]))
        self.assertIn("stale event head", errors)
        with self.aquarium() as aquarium:
            self.assertEqual((aquarium.tick, aquarium.stored_events), (2, 3))
            self.assertEqual(aquarium.genome.snapshot()["events"], [])

    def test_authored_empty_scenes_are_passed_to_both_opens(self):
        for width, sensory_mode, brain_mode in ((4, "toy_probe", "placeholder"),
                                                 (5, "toy_probe_v2", "developmental_controller")):
            with self.subTest(width=width, sensory_mode=sensory_mode):
                self.db = self.directory / (str(width) + ".sqlite3")
                draft = self.directory / (str(width) + ".json")
                draft.write_text(json.dumps({"schema": "mojum.world.point-room-draft/1",
                                             "width": width, "height": 4, "obstacles": [], "start": [1, 2]}))
                room, start = load_runtime_room(draft)
                options = dict(room=room, start=start, motor_mode="developmental_fixture",
                               sensory_mode=sensory_mode, brain_mode=brain_mode,
                               sensory_scene=ToyScene(width=width, height=4, toys=()))
                before = self.boot(**options)
                profile = ["--room-file", str(draft), "--motor-mode", "developmental_fixture",
                           "--sensory-mode", sensory_mode, "--brain-mode", brain_mode,
                           "--sensory-scene", "empty"]
                with patch("mojum.aquarium_genesis.Aquarium", wraps=Aquarium) as constructor:
                    result = self.invoke(self.command("acquire", *profile, head=before["journal_head"]))
                self.assertEqual(constructor.call_count, 2)
                for call in constructor.call_args_list:
                    self.assertEqual(call.kwargs["sensory_scene"].manifest(), options["sensory_scene"].manifest())
                with self.aquarium(**options) as aquarium:
                    self.assertEqual(aquarium.checkpoint()["journal_head"], result["journal_head"])
                    self.assertEqual(aquarium.step()["phenotype"]["parameters"]["heartbeat_bpm"], 75)
                original = self.db.read_bytes()
                wrong = self.command("status", *profile)
                wrong[wrong.index("--sensory-scene") + 1] = "default"
                self.reject(wrong)
                self.assertEqual(self.db.read_bytes(), original)

    def test_contradictory_scene_and_clock_profiles_are_rejected_without_writes(self):
        before = self.boot()
        original = self.db.read_bytes()
        for profile in (("--sensory-scene", "empty"), ("--dt-ns", "20000000"),
                        ("--neural-mode", "disabled"), ("--motor-mode", "developmental_fixture")):
            with self.subTest(profile=profile):
                self.reject(self.command("acquire", *profile, head=before["journal_head"]))
                self.assertEqual(self.db.read_bytes(), original)

    def test_custom_unicode_seed_nondefault_clock_and_ascii_receipts(self):
        seed = copy.deepcopy(SEED)
        seed["genome_id"] = "организм-雪"
        seed_path = self.directory / "seed.json"
        seed_path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
        with Aquarium(self.db, seed, GRAPH, agent_id=seed["genome_id"], dt_ns=20_000_000,
                      neural_mode="disabled") as aquarium:
            aquarium.step()
            head = aquarium.checkpoint()["journal_head"]
        args = self.command("acquire", "--genome", str(seed_path), "--dt-ns", "20000000",
                            "--neural-mode", "disabled", head=head)
        args[args.index("--event-id") + 1] = "изменение-雪"
        result = self.invoke(args)
        self.assertEqual(result["agent_id"], seed["genome_id"])
        self.assertEqual(result["sim_time_ns"], 20_000_000)
        with Aquarium(self.db, seed, GRAPH, agent_id=seed["genome_id"], dt_ns=20_000_000,
                      neural_mode="disabled") as aquarium:
            self.assertEqual(aquarium.genome.snapshot()["events"][0]["recorded_at_ns"], 20_000_000)

    def test_malformed_and_oversized_genome_inputs_fail_before_journal_access(self):
        seed_path = self.directory / "seed.json"
        valid = json.dumps(SEED)
        for payload in (b"\xff", b" " * (MAX_SEED_BYTES + 1), b"[" * 1500,
                        valid.replace('"inherited":', '"schema":"duplicate","inherited":').encode(),
                        valid.replace('"heartbeat_bpm": 60', '"heartbeat_bpm": NaN').encode()):
            with self.subTest(payload=payload[:30]):
                seed_path.write_bytes(payload)
                with patch("mojum.aquarium_genesis.Aquarium") as constructor:
                    self.reject(self.command("status", "--genome", str(seed_path)))
                    constructor.assert_not_called()
        self.assertFalse(self.db.exists())

    @unittest.skipUnless(hasattr(os, "mkfifo") and getattr(os, "O_NONBLOCK", 0), "POSIX FIFO required")
    def test_fifo_genome_refuses_promptly_in_real_process(self):
        fifo = self.directory / "seed.fifo"
        os.mkfifo(fifo)
        run = self.subprocess_command("mojum.aquarium_genesis", *self.command("status", "--genome", str(fifo)))
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn("regular file", run.stderr)
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
