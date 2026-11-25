"""Real-journal tests for explicit, human-declared identity integration."""

from contextlib import closing
import copy
import errno
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import mojum
from mojum import aquarium_identity
from mojum.aquarium import (
    Aquarium, V2A_ARCHIVE_MODE, V2B_MODE, V2B_WIRE_MODE,
    V3_MODE, V3_ESCAPE_MODE, V4_MODE,
)
from mojum.body_genetics import IDENTITY_SCHEMA
from mojum.genesis import ACQUISITION_SCHEMA
from mojum.noosphere import LocalNoosphere
from mojum.room_draft_runtime import load_runtime_room
from mojum.sensory_play import ToyScene


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(mojum.__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
NAME = "Данте 星"
ACTOR = "declared-human-fixture"
SIDECARS = ("", "-wal", "-shm", "-journal")


class AquariumIdentityCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root / "organism.sqlite3"

    def create(self, path=None, *, ticks=2, seed=None, **runtime):
        seed = SEED if seed is None else seed
        with Aquarium(path or self.db, seed, GRAPH,
                      agent_id=seed["genome_id"], **runtime) as live:
            for _ in range(ticks):
                live.step()
            return live.checkpoint()

    def standalone(self, path=None):
        path = path or self.db
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=DELETE").fetchone(),
                             ("delete",))
        if os.name == "posix":
            path.chmod(0o640)
        self.assertFalse(any(Path(str(path) + suffix).exists()
                             for suffix in SIDECARS[1:]))

    def files(self, path=None):
        path = path or self.db
        return {
            suffix: (candidate.read_bytes(), stat.S_IMODE(candidate.stat().st_mode))
            for suffix in SIDECARS
            if (candidate := Path(str(path) + suffix)).exists()
        }

    def run_cli(self, action, *args, path=None, encoding=None, timeout=15):
        env = dict(os.environ)
        # Follow the actual imported package, including an installed wheel.
        env["PYTHONPATH"] = str(PACKAGE_ROOT)
        if encoding is not None:
            env["PYTHONIOENCODING"] = encoding + ":strict"
            env["PYTHONUTF8"] = "0"
        return subprocess.run(
            [sys.executable, "-m", "mojum.aquarium_identity", action,
             "--db", str(path or self.db), *(str(arg) for arg in args)],
            cwd=self.root, env=env, capture_output=True, timeout=timeout,
        )

    def receipt(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        # All receipts remain representable on ASCII and legacy consoles.
        wire = result.stdout.decode("ascii")
        self.assertEqual(len(wire.splitlines()), 1)
        return json.loads(wire)

    def refused(self, result):
        self.assertEqual(result.returncode, 2, result.stderr.decode("utf-8", "replace"))
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"Traceback", result.stderr)

    def assert_receipt(self, receipt, checkpoint, *, action, events):
        expected = {
            "schema": "mojum.aquarium.identity-cli/1", "action": action,
            "stored_events": events,
            **{key: checkpoint[key] for key in (
                "agent_id", "tick", "sim_time_ns", "development_months",
                "journal_head", "display_name", "name_provenance", "trust",
            )},
        }
        self.assertEqual(receipt, expected)

    def assign(self, head, *, path=None, display_name=NAME, actor_id=ACTOR,
               **runtime):
        return aquarium_identity.assign_identity(
            path or self.db, SEED, GRAPH, display_name=display_name,
            actor_id=actor_id, expected_head=head, **runtime,
        )

    @staticmethod
    def readonly_factory(*args, **kwargs):
        if kwargs.get("read_only") is not True:
            raise AssertionError("rejected operation opened Aquarium writable")
        return Aquarium(*args, **kwargs)

    def test_status_replays_existing_history_without_writable_access(self):
        checkpoint = self.create()
        self.standalone()
        before = self.files()
        with patch.object(aquarium_identity, "Aquarium", side_effect=self.readonly_factory):
            receipt = aquarium_identity.identity_status(self.db, SEED, GRAPH)
        self.assert_receipt(receipt, checkpoint, action="status", events=3)
        self.assertEqual(self.files(), before)
        result = self.receipt(self.run_cli("status", "--expected-head", checkpoint["journal_head"]))
        self.assert_receipt(result, checkpoint, action="status", events=3)
        self.assertEqual(self.files(), before)

    def test_assign_commits_one_identity_event_and_cold_replays_without_physics(self):
        self.create()
        with Aquarium(self.db, SEED, GRAPH) as live:
            live.advance_development(6, reason="fixture semester")
            live.acquire({
                "schema": ACQUISITION_SCHEMA, "event_id": "identity-fixture-gene",
                "genome_id": SEED["genome_id"], "gene": "heartbeat_bpm", "delta": 15,
                "valid_from_tick": live.tick, "observed_at_ns": 0,
                "recorded_at_ns": live.tick * live.dt_ns,
                "source": "identity-test", "evidence": "synthetic fixture",
            })
            before = live.checkpoint()
            prior_events = list(live.store.iter_events(include_nonpublic=True))
        self.standalone()
        receipt = self.receipt(self.run_cli(
            "assign", "--display-name", NAME, "--actor-id", ACTOR,
            "--expected-head", before["journal_head"],
        ))
        with Aquarium(self.db, SEED, GRAPH, read_only=True) as named:
            after = named.checkpoint()
            self.assert_receipt(receipt, after, action="assign", events=len(prior_events) + 1)
            self.assertEqual(named.identity, {
                "schema": IDENTITY_SCHEMA, "agent_id": SEED["genome_id"],
                "display_name": NAME, "assigned_by": "human",
            })
            for key in ("tick", "sim_time_ns", "genome_revision", "world_revision",
                        "neural_state", "development_months", "manifest_sha256"):
                self.assertEqual(after[key], before[key], key)
            rows = list(named.store.iter_events(include_nonpublic=True))
            self.assertEqual(rows[:-1], prior_events)
            self.assertEqual(rows[-1]["event"]["topic"], "identity.assigned")
            self.assertEqual(rows[-1]["event"]["payload"]["actor_id"], ACTOR)
            self.assertEqual(rows[-1]["event"]["visibility"], "PRIVATE")
        status = self.receipt(self.run_cli("status"))
        self.assert_receipt(status, after, action="status", events=len(prior_events) + 1)
        with Aquarium(self.db, SEED, GRAPH) as continued:
            self.assertEqual(continued.checkpoint(), after)
            continued.step()
            self.assertEqual(continued.tick, before["tick"] + 1)
            self.assertEqual(continued.identity["display_name"], NAME)

    def test_unicode_receipts_on_ascii_and_cp1251_consoles(self):
        for encoding in ("ascii", "cp1251"):
            with self.subTest(encoding=encoding):
                path = self.root / (encoding + ".sqlite3")
                checkpoint = self.create(path)
                assigned = self.receipt(self.run_cli(
                    "assign", "--display-name", NAME, "--actor-id", ACTOR,
                    "--expected-head", checkpoint["journal_head"],
                    path=path, encoding=encoding,
                ))
                self.assertEqual(assigned["display_name"], NAME)
                self.assertEqual(assigned["name_provenance"], "human_declared_unverified")
                status = self.receipt(self.run_cli("status", path=path, encoding=encoding))
                self.assertEqual(status, dict(assigned, action="status"))

    def test_explicit_runtime_profiles_preserve_existing_configuration(self):
        profiles = (
            ("disabled", {"neural_mode": "disabled"}, ["--neural-mode", "disabled"]),
            ("controller", {"motor_mode": "developmental_fixture",
                            "brain_mode": "developmental_controller",
                            "sensory_mode": "toy_probe_v2",
                            "pulse_sum": "ordered", "dt_ns": 20_000_000},
             ["--motor-mode", "developmental_fixture", "--brain-mode", "developmental_controller",
              "--sensory-mode", "toy_probe_v2", "--pulse-sum", "ordered", "--dt-ns", "20000000"]),
        )
        for label, runtime, flags in profiles:
            with self.subTest(profile=label):
                path = self.root / (label + ".sqlite3")
                before = self.create(path, **runtime)
                receipt = self.receipt(self.run_cli(
                    "assign", "--display-name", NAME, "--actor-id", ACTOR,
                    "--expected-head", before["journal_head"], *flags, path=path,
                ))
                with Aquarium(path, SEED, GRAPH, read_only=True, **runtime) as reopened:
                    self.assert_receipt(receipt, reopened.checkpoint(), action="assign", events=4)
                    self.assertEqual(reopened.tick, before["tick"])
                    self.assertEqual(reopened.checkpoint()["world_revision"], before["world_revision"])

    def test_custom_room_empty_scene_and_genome_are_explicitly_replayable(self):
        room_file = self.root / "room.json"
        room_file.write_text(json.dumps({
            "schema": "mojum.world.point-room-draft/1", "width": 5, "height": 4,
            "obstacles": [], "start": [1, 2],
        }), encoding="utf-8")
        room, start = load_runtime_room(room_file)
        seed = copy.deepcopy(SEED)
        seed["genome_id"] = "custom-human-fixture"
        genome_file = self.root / "genome.json"
        genome_file.write_text(json.dumps(seed), encoding="utf-8")
        graph_file = self.root / "graph.json"
        graph_file.write_text(json.dumps(GRAPH), encoding="utf-8")
        runtime = {"room": room, "start": start, "motor_mode": "developmental_fixture",
                   "sensory_mode": "toy_probe", "temporal_mode": "range_guided",
                   "sensory_scene": ToyScene(width=room.width, height=room.height, toys=())}
        before = self.create(seed=seed, **runtime)
        self.standalone()
        files = self.files()
        flags = ["--genome", genome_file, "--graph", graph_file, "--room-file", room_file,
                 "--motor-mode", "developmental_fixture", "--sensory-mode", "toy_probe",
                 "--temporal-mode", "range_guided"]
        self.refused(self.run_cli("status", *flags))
        self.assertEqual(self.files(), files)
        receipt = self.receipt(self.run_cli(
            "assign", "--display-name", NAME, "--actor-id", ACTOR,
            "--expected-head", before["journal_head"], *flags, "--sensory-scene", "empty",
        ))
        with Aquarium(self.db, seed, GRAPH, agent_id=seed["genome_id"], read_only=True,
                      **runtime) as reopened:
            self.assert_receipt(receipt, reopened.checkpoint(), action="assign", events=4)

    def test_missing_and_invalid_assignment_arguments_preserve_history(self):
        checkpoint = self.create()
        self.standalone()
        before = self.files()
        base = ["--display-name", NAME, "--actor-id", ACTOR]
        cases = (
            base,
            [*base, "--expected-head", "0" * 64],
            [*base, "--expected-head", "A" * 64],
            ["--actor-id", ACTOR, "--expected-head", checkpoint["journal_head"]],
            ["--display-name", NAME, "--expected-head", checkpoint["journal_head"]],
            ["--display-name", "", "--actor-id", ACTOR,
             "--expected-head", checkpoint["journal_head"]],
            [*base, "--expected-head", checkpoint["journal_head"], "--dt-ns", "0"],
            [*base, "--expected-head", checkpoint["journal_head"], "--sensory-scene", "empty"],
        )
        for args in cases:
            with self.subTest(args=args):
                self.refused(self.run_cli("assign", *args))
                self.assertEqual(self.files(), before)
        self.refused(self.run_cli("status", "--expected-head", "0" * 64))
        self.assertEqual(self.files(), before)

    def test_invalid_names_and_actors_are_rejected_before_writable_open(self):
        checkpoint = self.create()
        self.standalone()
        before = self.files()
        cases = (
            {"display_name": ""}, {"display_name": " "}, {"display_name": "x" * 81},
            {"display_name": "bad\nname"}, {"display_name": "bad\u200bname"},
            {"display_name": "\ud800"}, {"actor_id": ""}, {"actor_id": " "},
            {"actor_id": "x" * 129}, {"actor_id": "bad\nactor"}, {"actor_id": "\ud800"},
            {"expected_head": None}, {"expected_head": ""}, {"expected_head": "0" * 64},
        )
        for change in cases:
            with self.subTest(change=repr(change)):
                args = {"display_name": NAME, "actor_id": ACTOR,
                        "expected_head": checkpoint["journal_head"], **change}
                with patch.object(aquarium_identity, "Aquarium", side_effect=self.readonly_factory):
                    with self.assertRaises((ValueError, OSError)):
                        aquarium_identity.assign_identity(self.db, SEED, GRAPH, **args)
                self.assertEqual(self.files(), before)

    def test_invalid_configuration_files_cannot_name_or_change_existing_history(self):
        checkpoint = self.create()
        self.standalone()
        before = self.files()
        for option, value, duplicate_key in (
            ("--genome", SEED, "genome_id"), ("--graph", GRAPH, "id"),
        ):
            valid = json.dumps(value).encode("utf-8")
            duplicate = (valid[:-1] + b"," + json.dumps(duplicate_key).encode("ascii")
                         + b":" + json.dumps(value[duplicate_key]).encode("ascii") + b"}")
            cases = {
                "duplicate": duplicate,
                "oversized": valid + b" " * (256 * 1024 + 1 - len(valid)),
                "nonfinite": valid[:-1] + b',"invalid_number":NaN}',
                "deep": valid[:-1] + b',"deep":' + b"[" * 70 + b"0" + b"]" * 70 + b"}",
                "invalid_utf8": valid + b"\xff",
                "malformed": b'{"unfinished":',
            }
            for label, wire in cases.items():
                with self.subTest(option=option, invalid=label):
                    config = self.root / (option[2:] + "-" + label + ".json")
                    config.write_bytes(wire)
                    self.refused(self.run_cli(
                        "assign", "--display-name", NAME, "--actor-id", ACTOR,
                        "--expected-head", checkpoint["journal_head"], option, config,
                    ))
                    self.assertEqual(self.files(), before)
        with Aquarium(self.db, SEED, GRAPH, read_only=True) as unchanged:
            self.assertEqual(unchanged.checkpoint(), checkpoint)
            self.assertIsNone(unchanged.identity)

    def test_exact_configuration_byte_limit_remains_usable(self):
        checkpoint = self.create()
        self.standalone()
        before = self.files()
        flags = []
        for name, value in (("genome", SEED), ("graph", GRAPH)):
            path = self.root / (name + "-at-limit.json")
            payload = json.dumps(value).encode("utf-8")
            path.write_bytes(payload + b" " * (256 * 1024 - len(payload)))
            self.assertEqual(path.stat().st_size, 256 * 1024)
            flags.extend(("--" + name, path))
        receipt = self.receipt(self.run_cli("status", *flags))
        self.assert_receipt(receipt, checkpoint, action="status", events=3)
        self.assertEqual(self.files(), before)

    def test_configuration_descriptor_closes_when_stream_wrapping_fails(self):
        config = self.root / "genome.json"
        config.write_text(json.dumps(SEED), encoding="utf-8")
        opened = []
        real_open = os.open

        def capture_descriptor(*args, **kwargs):
            descriptor = real_open(*args, **kwargs)
            opened.append(descriptor)
            return descriptor

        try:
            with patch.object(aquarium_identity.os, "open", side_effect=capture_descriptor), \
                 patch.object(aquarium_identity.os, "fdopen", side_effect=OSError("wrapping failed")):
                with self.assertRaisesRegex(OSError, "wrapping failed"):
                    aquarium_identity._load_config(config)
            self.assertEqual(len(opened), 1)
            with self.assertRaises(OSError) as closed:
                os.fstat(opened[0])
            self.assertEqual(closed.exception.errno, errno.EBADF)
        finally:
            # Keep a regressed implementation from leaking the probe handle
            # into unrelated tests while still reporting the ownership error.
            for descriptor in opened:
                try:
                    os.close(descriptor)
                except OSError as error:
                    if error.errno != errno.EBADF:
                        raise

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo")
                         and getattr(os, "O_NONBLOCK", 0), "POSIX nonblocking FIFO support")
    def test_special_journal_and_configuration_paths_fail_without_blocking(self):
        checkpoint = self.create()
        self.standalone()
        before = self.files()
        assignment = ("--display-name", NAME, "--actor-id", ACTOR,
                      "--expected-head", checkpoint["journal_head"])
        directory = self.root / "directory.sqlite3"
        directory.mkdir()
        linked = self.root / "linked.sqlite3"
        linked.symlink_to(self.db)
        journal_fifo = self.root / "journal.fifo"
        os.mkfifo(journal_fifo)
        for candidate in (directory, linked, journal_fifo):
            with self.subTest(journal=candidate.name):
                self.refused(self.run_cli("status", path=candidate, timeout=5))
                self.refused(self.run_cli("assign", *assignment, path=candidate, timeout=5))
                self.assertEqual(self.files(), before)
                self.assertFalse(any(Path(str(candidate) + suffix).exists()
                                     for suffix in SIDECARS[1:]))
        self.assertTrue(linked.is_symlink())
        self.assertEqual(list(directory.iterdir()), [])
        for option in ("--genome", "--graph"):
            with self.subTest(config=option):
                fifo = self.root / (option[2:] + ".fifo")
                os.mkfifo(fifo)
                self.refused(self.run_cli("assign", *assignment, option, fifo, timeout=5))
                self.assertTrue(stat.S_ISFIFO(fifo.stat().st_mode))
                self.assertEqual(self.files(), before)

    def test_already_named_and_exhausted_budget_fail_before_writable_open(self):
        checkpoint = self.create()
        with patch("mojum.aquarium.MAX_REPLAY_EVENTS", 3):
            self.standalone()
            before = self.files()
            with patch.object(aquarium_identity, "Aquarium", side_effect=self.readonly_factory):
                with self.assertRaises(ValueError):
                    self.assign(checkpoint["journal_head"])
            self.assertEqual(self.files(), before)
        receipt = self.assign(checkpoint["journal_head"])
        self.standalone()
        before = self.files()
        with patch.object(aquarium_identity, "Aquarium", side_effect=self.readonly_factory):
            with self.assertRaises(ValueError):
                self.assign(receipt["journal_head"], display_name="Second name")
        self.refused(self.run_cli(
            "assign", "--display-name", "Second name", "--actor-id", ACTOR,
            "--expected-head", receipt["journal_head"],
        ))
        self.assertEqual(self.files(), before)

    def test_invalid_existing_sources_are_never_created_or_repaired(self):
        for kind in ("missing", "empty", "foreign", "bootstrapless", "wrong_owner",
                     "malformed", "corrupt_frame", "unreplayable"):
            with self.subTest(kind=kind):
                path = self.root / (kind + ".sqlite3")
                head = "0" * 64
                if kind == "empty":
                    path.touch()
                elif kind == "foreign":
                    with closing(sqlite3.connect(path)) as db:
                        db.execute("CREATE TABLE user_notes (note TEXT)")
                        db.execute("INSERT INTO user_notes VALUES ('preserve')")
                        db.commit()
                elif kind == "bootstrapless":
                    with LocalNoosphere(path, SEED["genome_id"]):
                        pass
                    self.standalone(path)
                elif kind == "malformed":
                    path.write_bytes(b"not a SQLite database\x00fixture")
                elif kind in ("wrong_owner", "corrupt_frame", "unreplayable"):
                    seed = copy.deepcopy(SEED)
                    if kind == "wrong_owner":
                        seed["genome_id"] = "another-organism"
                    head = self.create(path, seed=seed)["journal_head"]
                    if kind == "corrupt_frame":
                        with closing(sqlite3.connect(path)) as db:
                            db.execute("DROP TRIGGER noosphere_no_update")
                            db.execute("UPDATE events SET event_hash = ? WHERE seq = 2", ("f" * 64,))
                            db.commit()
                    elif kind == "unreplayable":
                        with LocalNoosphere(path, SEED["genome_id"]) as store:
                            event = copy.deepcopy(store.dataset(include_nonpublic=True, limit=3)
                                                  ["projection"]["items"][-1]["event"])
                            event["event_id"] = "unexpected-event"
                            event["topic"] = "unexpected.topic"
                            head = store.append(event, expected_head=store.head())
                    self.standalone(path)
                if path.exists() and os.name == "posix":
                    path.chmod(0o640)
                before = self.files(path)
                with patch.object(aquarium_identity, "Aquarium", side_effect=self.readonly_factory):
                    with self.assertRaises((ValueError, OSError)):
                        aquarium_identity.identity_status(path, SEED, GRAPH)
                    with self.assertRaises((ValueError, OSError)):
                        self.assign(head, path=path)
                self.refused(self.run_cli("status", path=path))
                self.refused(self.run_cli(
                    "assign", "--display-name", NAME, "--actor-id", ACTOR,
                    "--expected-head", head, path=path,
                ))
                self.assertEqual(self.files(path), before)

    def test_local_cell_histories_allow_status_but_refuse_naming(self):
        for selector in (V2B_MODE, V3_MODE, V3_ESCAPE_MODE, V4_MODE, V2B_WIRE_MODE):
            with self.subTest(selector=selector):
                path = self.root / (selector + ".sqlite3")
                creation_mode = V2B_MODE if selector == V2B_WIRE_MODE else selector
                checkpoint = self.create(path, motor_mode=creation_mode)
                self.standalone(path)
                before = self.files(path)
                receipt = self.receipt(self.run_cli("status", "--motor-mode", selector, path=path))
                self.assert_receipt(receipt, checkpoint, action="status", events=3)
                with patch.object(aquarium_identity, "Aquarium", side_effect=self.readonly_factory):
                    with self.assertRaises(ValueError):
                        self.assign(checkpoint["journal_head"], path=path, motor_mode=selector)
                self.refused(self.run_cli(
                    "assign", "--display-name", NAME, "--actor-id", ACTOR,
                    "--expected-head", checkpoint["journal_head"], "--motor-mode", selector,
                    path=path,
                ))
                self.assertEqual(self.files(path), before)
        # The archival selector is read-only by the existing Aquarium contract.
        checkpoint = self.create()
        self.standalone()
        before = self.files()
        with patch.object(aquarium_identity, "Aquarium", side_effect=self.readonly_factory):
            with self.assertRaises(ValueError):
                self.assign(checkpoint["journal_head"], motor_mode=V2A_ARCHIVE_MODE)
        self.assertEqual(self.files(), before)

    def test_readonly_to_writable_races_cannot_bootstrap_or_name_new_history(self):
        for change in ("remove", "replace_empty", "replace_foreign", "advance", "name"):
            with self.subTest(change=change):
                path = self.root / (change + ".sqlite3")
                checkpoint = self.create(path)
                self.standalone(path)
                observed = {}

                def race(*args, **kwargs):
                    if kwargs.get("read_only") is not True and not observed:
                        self.assertEqual(kwargs.get("external_expected_head"), checkpoint["journal_head"])
                        if change == "remove":
                            path.unlink()
                        elif change.startswith("replace"):
                            replacement = self.root / (change + "-replacement.sqlite3")
                            if change == "replace_empty":
                                replacement.touch()
                            else:
                                with closing(sqlite3.connect(replacement)) as db:
                                    db.execute("CREATE TABLE user_notes (note TEXT)")
                            os.replace(replacement, path)
                            if os.name == "posix":
                                path.chmod(0o640)
                        else:
                            with Aquarium(path, SEED, GRAPH) as concurrent:
                                if change == "advance":
                                    concurrent.step()
                                else:
                                    concurrent.assign_human_name({
                                        "schema": IDENTITY_SCHEMA, "agent_id": SEED["genome_id"],
                                        "display_name": "Other declared name", "assigned_by": "human",
                                    }, actor_id="other-caller")
                            self.standalone(path)
                        observed["files"] = self.files(path)
                    return Aquarium(*args, **kwargs)

                with patch.object(aquarium_identity, "Aquarium", side_effect=race):
                    with self.assertRaises((ValueError, OSError)):
                        self.assign(checkpoint["journal_head"], path=path)
                self.assertIn("files", observed)
                self.assertEqual(self.files(path), observed["files"])
                if change in ("advance", "name"):
                    with Aquarium(path, SEED, GRAPH, read_only=True) as reopened:
                        self.assertEqual(reopened.stored_events, 4)
                        self.assertEqual(reopened.identity["display_name"] if reopened.identity else None,
                                         "Other declared name" if change == "name" else None)

    def test_receipt_count_stays_with_checkpoint_if_another_writer_advances(self):
        for action in ("status", "assign"):
            with self.subTest(action=action):
                path = self.root / (action + ".sqlite3")
                checkpoint = self.create(path)
                checkpoint_method = Aquarium.checkpoint
                advanced = []
                reported = []

                def checkpoint_then_advance(instance):
                    result = checkpoint_method(instance)
                    is_receipt = instance._read_only if action == "status" else not instance._read_only
                    if is_receipt and not advanced:
                        advanced.append(True)
                        reported.append(result)
                        with Aquarium(path, SEED, GRAPH) as writer:
                            writer.step()
                    return result

                with patch.object(Aquarium, "checkpoint", checkpoint_then_advance):
                    if action == "status":
                        receipt = aquarium_identity.identity_status(path, SEED, GRAPH)
                    else:
                        receipt = self.assign(checkpoint["journal_head"], path=path)
                self.assertTrue(advanced)
                expected_count = 3 if action == "status" else 4
                self.assert_receipt(receipt, reported[0], action=action, events=expected_count)
                with Aquarium(path, SEED, GRAPH, read_only=True) as newest:
                    self.assertEqual(newest.stored_events, expected_count + 1)
                    self.assertNotEqual(newest.checkpoint()["journal_head"], receipt["journal_head"])


if __name__ == "__main__":
    unittest.main()
