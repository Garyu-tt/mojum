"""Opt-in new-journal v2A -> v2B transfer on actual pinned #112 SQLite.

Uses only disposable synthetic histories. A source history is never rewritten,
and the final destination must remain ABSENT when verification fails.
"""
from __future__ import annotations

from contextlib import closing
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum import local_cell_transfer as transfer_module
from mojum.aquarium import Aquarium, AquariumError, V2A_ARCHIVE_MODE, V2B_MODE, V2B_WIRE_MODE, V3_MODE
from mojum.aquarium_trace_export import export_private_trace
from mojum.archived_policy_v2a import LocalCellExplorer
from mojum.cell_policy_v2 import CellExplorer, LocalCellError
from mojum.local_cell_sense import Proprioception
from mojum.migration_v2a_v2b import (
    SCHEMA, TransferError, transfer_v2a_to_v2b,
)
from mojum.migration_v2b_v3 import transfer_v2b_to_v3
from mojum.world_room import Room

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
SOURCE112 = Path(os.environ["MOJUM_ARCHIVE112_SOURCE"]).resolve() if os.environ.get(
    "MOJUM_ARCHIVE112_SOURCE") else None
SOURCE115 = Path(os.environ["MOJUM_ARCHIVE115_SOURCE"]).resolve() if os.environ.get(
    "MOJUM_ARCHIVE115_SOURCE") else None


def fingerprint(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                                 uri=True)) as db:
        data = db.execute(
            "SELECT seq,event_id,prev_hash,event_hash,wire_json "
            "FROM events ORDER BY seq"
        ).fetchall()
    import hashlib
    encoded = json.dumps(data, ensure_ascii=True, separators=(",", ":")).encode()
    return len(data), hashlib.sha256(encoded).hexdigest(), data[-1][3]


class LocalCellSnapshotTests(unittest.TestCase):
    def test_rejects_blocked_rest_and_repeated_blocks(self):
        for policy in (CellExplorer, LocalCellExplorer):
            for action, counters in (("rest", [2, 1]), ("x+", [2, 2])):
                with self.subTest(policy=policy.__name__, action=action):
                    snapshot = policy().snapshot()
                    snapshot.update(last_tick=1, updates=2, records={"0:0": {action: counters}})
                    with self.assertRaisesRegex(ValueError, "impossible blocked motor history"):
                        policy.from_snapshot(snapshot)


class LocalCellEvidenceAdmissionTests(unittest.TestCase):
    """A live policy must never record evidence its own from_snapshot rejects."""

    def _sense(self, tick, pulse=False):
        return Proprioception(tick=tick, revision="a" * 64, x=0.0, y=0.0,
                              pulse=pulse)

    def test_observe_refuses_blocked_rest_in_canonical_policy(self):
        policy = CellExplorer()
        # Every cardinal move already blocked in this cell: decide() can only
        # fall back to rest, which the point room can never report blocked.
        policy.records = {"0:0": {
            action: [1, 1] for action in ("x+", "y+", "x-", "y-")
        }}
        sense = self._sense(0)
        intent = policy.decide(sense)
        self.assertEqual(intent["action"], "rest")
        with self.assertRaisesRegex(LocalCellError, "impossible blocked motor history"):
            policy.observe(sense, intent, True)
        self.assertEqual(policy.records["0:0"].get("rest"), None)
        self.assertEqual(policy.updates, 0)

    def test_observe_refuses_reblocking_an_inhibited_action(self):
        policy = CellExplorer()
        policy.records = {"0:0": {"x+": [1, 1]}}
        sense = self._sense(0)
        intent = policy.decide(sense)
        self.assertNotEqual(intent["action"], "x+")
        forged = dict(intent, action="x+", dx=0.04, dy=0.0)
        # decide() would never return this intent; observe must still refuse to
        # inflate an already-blocked action twice instead of trusting callers.
        with self.assertRaisesRegex(LocalCellError, "stale or fabricated"):
            policy.observe(sense, forged, True)

    def test_observe_still_records_ordinary_measured_outcomes(self):
        policy = CellExplorer()
        sense = self._sense(0)
        intent = policy.decide(sense)
        outcome = policy.observe(sense, intent, intent["action"] != "rest")
        self.assertEqual(outcome["total_updates"], 1)
        CellExplorer.from_snapshot(policy.snapshot())


class TransferDestinationSidecarTests(unittest.TestCase):
    """A new main file must not inherit another journal's SQLite sidecars."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = self.root / "source-v2b.sqlite3"
        with Aquarium(self.source, SEED, GRAPH, motor_mode=V2B_MODE) as source:
            source.step()
            source.step()
            self.head = source.checkpoint()["journal_head"]
        with closing(sqlite3.connect(self.source)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")
        self.source_bytes = self.source.read_bytes()

    def migrate(self, target):
        return transfer_v2b_to_v3(
            self.source, target, expected_source_head=self.head,
            seed=SEED, graph=GRAPH, create_new_journal=True,
        )

    def assert_source_unchanged(self):
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.assertFalse(list(self.root.glob(".mojum-local-cell-convert-*")))

    def orphaned_wal(self, target, *, include_shm):
        # Preserve real committed WAL bytes while the writer is still open.
        # Restore them after orderly close/removal of the old main to model
        # a stale destination left by incomplete recovery or manual cleanup.
        with Aquarium(target, SEED, GRAPH, motor_mode=V3_MODE) as previous:
            for _ in range(4):
                previous.step()
            saved = {"-wal": Path(str(target) + "-wal").read_bytes()}
            if include_shm:
                saved["-shm"] = Path(str(target) + "-shm").read_bytes()
        target.unlink()
        self.assertGreater(len(saved["-wal"]), 32)
        for suffix, data in saved.items():
            Path(str(target) + suffix).write_bytes(data)
        return saved

    def test_real_orphaned_wal_cannot_overlay_a_successful_transfer(self):
        for include_shm in (False, True):
            with self.subTest(include_shm=include_shm):
                target = self.root / f"orphan-{include_shm}.sqlite3"
                saved = self.orphaned_wal(target, include_shm=include_shm)
                with self.assertRaisesRegex(TransferError, "SQLite sidecars"):
                    self.migrate(target)
                self.assertFalse(os.path.lexists(target))
                for suffix, data in saved.items():
                    self.assertEqual(Path(str(target) + suffix).read_bytes(), data)
                self.assert_source_unchanged()

    def test_existing_sidecar_files_and_directories_fail_before_source_open(self):
        for suffix in ("-wal", "-shm", "-journal"):
            for directory in (False, True):
                with self.subTest(suffix=suffix, directory=directory):
                    target = self.root / f"target{suffix}-{directory}.sqlite3"
                    sidecar = Path(str(target) + suffix)
                    if directory:
                        sidecar.mkdir()
                        sentinel = sidecar / "foreign.txt"
                    else:
                        sentinel = sidecar
                    sentinel.write_bytes(b"preserve foreign artifact")
                    with patch.object(transfer_module, "_source_manifest",
                                      wraps=transfer_module._source_manifest) as source_open:
                        with self.assertRaisesRegex(TransferError, "SQLite sidecars"):
                            self.migrate(target)
                    source_open.assert_not_called()
                    self.assertFalse(os.path.lexists(target))
                    self.assertEqual(sentinel.read_bytes(), b"preserve foreign artifact")
                    self.assert_source_unchanged()

    def test_dangling_sidecar_symlinks_are_preserved_and_rejected(self):
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                target = self.root / f"symlink{suffix}.sqlite3"
                sidecar = Path(str(target) + suffix)
                missing = self.root / f"missing{suffix}"
                try:
                    sidecar.symlink_to(missing)
                except (NotImplementedError, OSError) as exc:
                    self.skipTest(f"symlink creation unavailable: {exc}")
                with self.assertRaisesRegex(TransferError, "SQLite sidecars"):
                    self.migrate(target)
                self.assertTrue(sidecar.is_symlink())
                self.assertEqual(sidecar.readlink(), missing)
                self.assertFalse(missing.exists())
                self.assertFalse(os.path.lexists(target))
                self.assert_source_unchanged()

    def test_sidecar_appearing_during_conversion_prevents_publication(self):
        verify_source = transfer_module._verify_source
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                target = self.root / f"late{suffix}.sqlite3"
                sidecar = Path(str(target) + suffix)

                def appear_after_verification(*args):
                    verify_source(*args)
                    sidecar.write_bytes(b"arrived during transfer")

                with patch.object(transfer_module, "_verify_source", appear_after_verification):
                    with self.assertRaisesRegex(TransferError, "SQLite sidecars"):
                        self.migrate(target)
                self.assertFalse(os.path.lexists(target))
                self.assertEqual(sidecar.read_bytes(), b"arrived during transfer")
                self.assert_source_unchanged()

    def test_exclusive_publication_still_preserves_a_competing_main_file(self):
        target = self.root / "competing.sqlite3"
        original_link = os.link

        def competing_create(staged, destination):
            self.assertEqual(destination, target)
            target.write_bytes(b"another writer owns this path")
            return original_link(staged, destination)

        with patch.object(transfer_module.os, "link", competing_create):
            with self.assertRaisesRegex(TransferError, "refusing overwrite"):
                self.migrate(target)
        self.assertEqual(target.read_bytes(), b"another writer owns this path")
        self.assert_source_unchanged()

    def test_clean_destination_still_reopens_at_the_reported_head(self):
        target = self.root / "clean.sqlite3"
        result = self.migrate(target)
        self.assertEqual(result["status"], "EXPLICIT_VERIFIED_NEW_JOURNAL")
        self.assertEqual(result["destination_events"], 3)
        with Aquarium(target, SEED, GRAPH, motor_mode=V3_MODE, read_only=True,
                      external_expected_head=result["destination_head"]) as reopened:
            self.assertEqual(reopened.tick, 2)
            self.assertEqual(reopened.store.count(), result["destination_events"])
            self.assertEqual(reopened.checkpoint()["journal_head"], result["destination_head"])
        self.assert_source_unchanged()

    def test_cli_refuses_stale_wal_without_a_success_receipt(self):
        target = self.root / "cli.sqlite3"
        saved = self.orphaned_wal(target, include_shm=False)
        result = subprocess.run(
            [sys.executable, "-m", "mojum.migration_v2b_v3",
             "--source", str(self.source), "--destination", str(target),
             "--genome", str(ROOT / "examples/genesis_seed.json"),
             "--graph", str(ROOT / "examples/first_loop.json"),
             "--expected-source-head", self.head, "--create-new-journal"],
            cwd=self.root, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("SQLite sidecars", result.stderr)
        self.assertFalse(os.path.lexists(target))
        self.assertEqual(Path(str(target) + "-wal").read_bytes(), saved["-wal"])
        self.assert_source_unchanged()


@unittest.skipUnless(SOURCE112 is not None,
                     "pinned #112 repository required for true cross-source QA")
class ExplicitSQLiteV2TransferQA(unittest.TestCase):
    def setUp(self):
        self.assertTrue((SOURCE112 / "src/mojum/developmental_policy_v2.py").is_file())
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = self.root / "source112.private.sqlite3"
        self.target = self.root / "new-v2b.private.sqlite3"

    def create_source(self, ticks=25):
        env = os.environ.copy()
        env["PYTHONPATH"] = str((SOURCE112 / "src").resolve())
        proc = subprocess.run(
            [sys.executable, "-m", "mojum.aquarium",
             "--db", str(self.source), "--ticks", str(ticks),
             "--exploration-mode", "goal_free",
             "--motor-policy", "local_cell_v2",
             "--genome", str(SOURCE112 / "examples/genesis_seed.json"),
             "--graph", str(SOURCE112 / "examples/first_loop.json")],
            cwd=SOURCE112, env=env, capture_output=True, text=True,
            timeout=45,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-1000:])
        saved = json.loads(proc.stdout)
        self.assertEqual(saved["tick"], ticks)
        self.assertEqual(saved["stored_events"], ticks + 1)
        return saved["journal_head"]

    def migrate(self, head, **options):
        params = {
            "expected_source_head": head,
            "seed": SEED, "graph": GRAPH,
            "create_new_journal": True,
        }
        params.update(options)
        return transfer_v2a_to_v2b(self.source, self.target, **params)

    def test_actual_v2a_to_v2b_transfer_with_cold_replay_and_independent_resume(self):
        expected = self.create_source(120)
        original = fingerprint(self.source)
        result = self.migrate(expected)
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["status"], "EXPLICIT_VERIFIED_NEW_JOURNAL")
        self.assertEqual(result["source_head"], expected)
        self.assertEqual(result["source_events"], 121)
        self.assertEqual(result["destination_events"], 121)
        self.assertEqual(result["compared_frames"], 120)
        self.assertEqual(result["source_policy_schema"],
                         "mojum.developmental.explorer.local-cell/2")
        self.assertEqual(result["destination_policy_schema"],
                         "mojum.developmental.local-cell/2")
        self.assertEqual(fingerprint(self.source), original)
        self.assertTrue(self.target.is_file())
        self.assertNotEqual(fingerprint(self.target)[1], original[1])
        self.assertNotEqual(result["destination_head"], result["source_head"])
        if os.name == "posix":
            self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)

        with Aquarium(self.target, SEED, GRAPH,
                      motor_mode=V2B_MODE) as reopened:
            self.assertEqual(reopened.tick, 120)
            self.assertEqual(reopened.store.count(), 121)
            self.assertEqual(reopened.store.head(), result["destination_head"])
            self.assertEqual(reopened.manifest["developmental_policy"],
                             "local_cell_v2")
            self.assertNotIn("motor_policy", reopened.manifest)
            reopened.step()
            self.assertEqual(reopened.tick, 121)
        self.assertEqual(fingerprint(self.source), original)

        # The source remains fully readable by the original archival contract.
        with Aquarium(self.source, SEED, GRAPH,
                      motor_mode=V2A_ARCHIVE_MODE,
                      external_expected_head=expected) as archived:
            self.assertEqual(archived.tick, 120)
            self.assertEqual(archived.store.head(), expected)
            with self.assertRaisesRegex(AquariumError, "read-only"):
                archived.step()
            with self.assertRaisesRegex(AquariumError, "read-only"):
                archived.advance_development()
        trace = self.root / "archived.private.jsonl"
        report = export_private_trace(
            self.source, trace, SEED, GRAPH, motor_mode=V2A_ARCHIVE_MODE,
            allow_private_export=True, expected_journal_head=expected,
            start_tick=119, frame_limit=1,
        )
        self.assertEqual((report["frames"], report["journal_head"]), (1, expected))
        self.assertEqual(fingerprint(self.source), original)

    def original_v2b(self, db, ticks):
        proc = subprocess.run(
            [sys.executable, "-m", "mojum.aquarium", "--db", str(db),
             "--ticks", str(ticks), "--exploration-mode", "goal_free",
             "--developmental-policy", "local_cell_v2"],
            cwd=SOURCE115, env=dict(os.environ, PYTHONPATH=str(SOURCE115 / "src")),
            encoding="utf-8", capture_output=True, check=True, timeout=45,
        )
        return json.loads(proc.stdout)


    @unittest.skipUnless(SOURCE115 is not None, "original #115 target runtime required")
    def test_original_v2b_runtime_resumes_new_journal(self):
        head = self.create_source(120)
        original = self.source.read_bytes()
        transfer = self.migrate(head)

        prior = self.original_v2b(self.target, 0)
        self.assertEqual((prior["tick"], prior["stored_events"]), (120, 121))
        self.assertEqual(prior["journal_head"], transfer["destination_head"])
        after = self.original_v2b(self.target, 1)
        self.assertEqual((after["tick"], after["stored_events"]), (121, 122))
        self.assertNotEqual(after["journal_head"], prior["journal_head"])
        self.assertEqual(after, self.original_v2b(self.root / "native-control.sqlite3", 121))
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(fingerprint(self.source)[2], head)

    @unittest.skipUnless(SOURCE115 is not None, "original #115 target runtime required")
    def test_b_selectors_resume_original_v2b_journal_and_native_writer_can_continue(self):
        original = self.original_v2b(self.target, 12)
        for policy in (V2B_MODE, V2B_WIRE_MODE):
            with self.subTest(policy=policy), Aquarium(
                self.target, SEED, GRAPH, motor_mode=policy, external_expected_head=original["journal_head"],
            ) as resumed:
                self.assertEqual(resumed.tick, 12)
                self.assertEqual(resumed.manifest["developmental_policy"], V2B_WIRE_MODE)
                self.assertEqual(resumed.checkpoint()["journal_head"], original["journal_head"])
        with Aquarium(self.target, SEED, GRAPH, motor_mode=V2B_MODE) as resumed:
            resumed.step()
            appended = resumed.checkpoint()
        native = self.original_v2b(self.target, 1)
        self.assertEqual(native["tick"], 14)
        self.assertNotEqual(native["journal_head"], appended["journal_head"])
        self.assertEqual(native, self.original_v2b(self.root / "native-round-trip.sqlite3", 14))

    def test_legacy_name_cannot_birth_journal_and_new_cli_name_preserves_wire(self):
        command = [sys.executable, "-m", "mojum.aquarium", "--db", str(self.target),
                   "--ticks", "2", "--motor-mode"]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        old = subprocess.run([*command, V2B_WIRE_MODE], cwd=ROOT, env=env,
                             encoding="utf-8", capture_output=True, timeout=35)
        self.assertNotEqual(old.returncode, 0)
        self.assertIn("requires an existing journal", old.stderr)
        self.assertFalse(self.target.exists())
        with self.assertRaisesRegex(AquariumError, "requires an existing journal"):
            Aquarium(self.target, SEED, GRAPH, motor_mode=V2B_WIRE_MODE)
        self.assertFalse(self.target.exists())
        created = json.loads(subprocess.check_output([*command, V2B_MODE], cwd=ROOT, env=env,
                                                    encoding="utf-8", timeout=35))
        self.assertEqual((created["tick"], created["stored_events"]), (2, 3))
        with Aquarium(self.target, SEED, GRAPH, motor_mode=V2B_WIRE_MODE) as legacy:
            self.assertEqual(legacy.manifest["developmental_policy"], V2B_WIRE_MODE)
            self.assertEqual(legacy.checkpoint()["journal_head"], created["journal_head"])

    def test_b_selectors_refuse_v2a_before_changing_sqlite_bytes(self):
        self.create_source(4)
        with closing(sqlite3.connect(self.source)) as db:
            self.assertEqual(db.execute("pragma journal_mode=delete").fetchone()[0], "delete")
        before = self.source.read_bytes()
        for policy in (V2B_MODE, V2B_WIRE_MODE):
            with self.subTest(policy=policy), self.assertRaisesRegex(AquariumError, "mismatch"):
                Aquarium(self.source, SEED, GRAPH, motor_mode=policy)
            self.assertEqual(self.source.read_bytes(), before)

    def test_json_type_change_cannot_publish_new_journal(self):
        head = self.create_source(4)
        original = self.source.read_bytes()
        original_step = Aquarium.step

        def return_type_changed_frame(instance, *args, **kwargs):
            frame = original_step(instance, *args, **kwargs)
            frame["world"]["blocked"] = int(frame["world"]["blocked"])
            return frame

        with patch.object(Aquarium, "step", return_type_changed_frame):
            with self.assertRaisesRegex(TransferError, "observations diverge"):
                self.migrate(head)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.source.read_bytes(), original)

    def test_wrong_head_or_missing_explicit_optin_cannot_create_destination(self):
        actual = self.create_source(9)
        before = fingerprint(self.source)
        for changes in (
            {"create_new_journal": False},
            {"expected_source_head": "f" * 64},
            {"expected_source_head": "bad"},
            {"max_ticks": 3},
            {"max_ticks": True},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises((TransferError, AquariumError,
                                        ValueError)):
                    self.migrate(actual, **changes)
                self.assertFalse(self.target.exists())
                self.assertEqual(fingerprint(self.source), before)

    def test_existing_output_does_not_change_and_source_cannot_be_destination(self):
        head = self.create_source(4)
        self.target.write_bytes(b"keep exact existing output")
        with self.assertRaisesRegex(TransferError, "must not already exist"):
            self.migrate(head)
        self.assertEqual(self.target.read_bytes(), b"keep exact existing output")
        with self.assertRaises(TransferError):
            transfer_v2a_to_v2b(
                self.source, self.source, expected_source_head=head,
                seed=SEED, graph=GRAPH, create_new_journal=True)
        self.assertEqual(fingerprint(self.source)[2], head)

    def test_forged_source_json_or_wrong_seed_fails_without_target(self):
        expected = self.create_source(10)
        baseline = fingerprint(self.source)
        with self.assertRaises((AquariumError, ValueError)):
            wrong = copy.deepcopy(SEED)
            wrong["genome_id"] = "other-agent"
            self.migrate(expected, seed=wrong)
        self.assertFalse(self.target.exists())
        self.assertEqual(fingerprint(self.source), baseline)

        copied = self.root / "copied-and-tampered.sqlite3"
        with closing(sqlite3.connect(self.source.resolve().as_uri() + "?mode=ro",
                                     uri=True)) as src:
            with closing(sqlite3.connect(copied)) as dst:
                src.backup(dst)
        with closing(sqlite3.connect(copied)) as conn:
            conn.execute("DROP TRIGGER noosphere_no_update")
            wire = conn.execute("SELECT wire_json FROM events WHERE seq=1").fetchone()[0]
            conn.execute("UPDATE events SET wire_json=? WHERE seq=1",
                         ('{"event_id":"forged-duplicate",' + wire[1:],))
            conn.commit()
        with self.assertRaises((AquariumError, ValueError)):
            transfer_v2a_to_v2b(
                copied, self.target, expected_source_head=expected,
                seed=SEED, graph=GRAPH, create_new_journal=True)
        self.assertFalse(self.target.exists())
        self.assertEqual(fingerprint(self.source), baseline)

    def test_disagreeing_policy_does_not_publish_partial_new_journal(self):
        head = self.create_source(16)
        source_snapshot = fingerprint(self.source)

        # Valid but intentionally different motor policy, injected only into
        # the destination candidate, cannot pass per-frame equivalence.
        def frozen_rest(self, sense):
            return {
                "action": "rest", "dx": 0.0, "dy": 0.0,
                "expected_revision": sense.revision,
                "tick": sense.tick, "zone": self._zone(sense),
                "source": "bounded_goal_free_local_cell_motor_play_v2",
            }

        with patch.object(CellExplorer, "decide", frozen_rest):
            with self.assertRaisesRegex(TransferError, "diverge"):
                self.migrate(head)
        self.assertFalse(self.target.exists())
        self.assertEqual(fingerprint(self.source), source_snapshot)

    def test_cli_requires_explicit_optin_and_exact_trusted_head(self):
        head = self.create_source(4)
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        args = [
            sys.executable, "-m", "mojum.migration_v2a_v2b",
            "--source", str(self.source), "--destination", str(self.target),
            "--genome", str(ROOT / "examples/genesis_seed.json"),
            "--graph", str(ROOT / "examples/first_loop.json"),
            "--expected-source-head", head,
        ]
        failure = subprocess.run(args, cwd=ROOT, env=env, capture_output=True,
                                 text=True, timeout=35)
        self.assertNotEqual(failure.returncode, 0)
        self.assertIn("explicit create_new_journal", failure.stderr)
        self.assertFalse(self.target.exists())
        done = subprocess.run(args + ["--create-new-journal"], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=35)
        self.assertEqual(done.returncode, 0, done.stderr[-1500:])
        metadata = json.loads(done.stdout)
        self.assertEqual(metadata["source_head"], head)
        self.assertEqual(metadata["compared_frames"], 4)
        self.assertEqual(fingerprint(self.source)[2], head)


if __name__ == "__main__":
    unittest.main()
