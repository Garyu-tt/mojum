"""Exact #112 v2A source history reopened by #125 host in SQLite mode=ro.

Cross-source tests require MOJUM_ARCHIVE112_SOURCE to point at a pinned
git checkout. The dedicated workflow always supplies it and MUST run all
tests; ordinary full-regression jobs execute store-only controls and skip
external-source tests explicitly rather than pretending interoperability.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError, ARCHIVE_MODE
from mojum.archived_policy_v2a import SCHEMA as V2A_SCHEMA
from mojum.noosphere import LocalNoosphere, NoosphereError

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
PINNED_SOURCE = Path(os.environ["MOJUM_ARCHIVE112_SOURCE"]).resolve() if os.environ.get(
    "MOJUM_ARCHIVE112_SOURCE") else None


def run_cli(source, db, *, policy, ticks, extra=()):
    env = os.environ.copy()
    env["PYTHONPATH"] = str((source / "src").resolve())
    if source == ROOT:
        flag, name = "--developmental-policy", policy
    else:
        flag = "--motor-policy"
        name = "local_cell_v2" if policy == "local_cell_v2_a_archive" else policy
    args = [
        sys.executable, "-m", "mojum.aquarium", "--db", str(db),
        "--ticks", str(ticks), "--exploration-mode", "goal_free",
        "--genome", str(source / "examples/genesis_seed.json"),
        "--graph", str(source / "examples/first_loop.json"),
        flag, name, *extra,
    ]
    proc = subprocess.run(args, cwd=source, env=env, text=True,
                          capture_output=True, timeout=50, check=False)
    if proc.returncode != 0:
        return None, proc.stderr
    return json.loads(proc.stdout), ""


def rows_fingerprint(db):
    # Check immutable logical event rows, not SQLite WAL/checkpoint page layout.
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as cx:
        data = cx.execute(
            "SELECT seq,event_id,prev_hash,event_hash,wire_json "
            "FROM events ORDER BY seq"
        ).fetchall()
    raw = json.dumps(data, sort_keys=True, ensure_ascii=True,
                     separators=(",", ":")).encode("utf-8")
    return len(data), hashlib.sha256(raw).hexdigest(), data[-1][3]


class ReadOnlySQLiteStoreUnit(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "sealed.sqlite3"

    def test_readonly_store_preserves_private_events_and_disallows_append(self):
        with LocalNoosphere(self.db, "dante-synthetic") as writable:
            empty_head = writable.head()
            self.assertEqual(writable.count(), 0)
        with LocalNoosphere(self.db, "dante-synthetic", read_only=True) as sealed:
            self.assertEqual(sealed.head(), empty_head)
            self.assertEqual(sealed._db.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaisesRegex(NoosphereError, "read-only archive"):
                sealed.append({}, expected_head=empty_head)
        with self.assertRaisesRegex(NoosphereError, "owner or schema mismatch"):
            LocalNoosphere(self.db, "different-person", read_only=True)
        with LocalNoosphere(self.db, "dante-synthetic") as reopened:
            self.assertEqual(reopened.count(), 0)

    def test_readonly_store_requires_existing_non_symlink_path(self):
        with self.assertRaisesRegex(NoosphereError, "must already exist"):
            LocalNoosphere(self.db, "dante-synthetic", read_only=True)
        self.assertFalse(self.db.exists())
        with self.assertRaisesRegex(NoosphereError, "read_only must be boolean"):
            LocalNoosphere(self.db, "dante-synthetic", read_only="yes")
        self.assertFalse(self.db.exists())


@unittest.skipUnless(PINNED_SOURCE is not None,
                     "requires MOJUM_ARCHIVE112_SOURCE immutable git checkout")
class V2AArchivedSQLiteInterop(unittest.TestCase):
    def setUp(self):
        self.assertTrue((PINNED_SOURCE / "src/mojum/developmental_policy_v2.py").is_file())
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "source112.private.sqlite3"

    def create_source_a(self, ticks=12):
        result, error = run_cli(
            PINNED_SOURCE, self.db,
            policy="local_cell_v2_a_archive", ticks=ticks)
        self.assertIsNotNone(result, error[-900:])
        self.assertEqual(result["tick"], ticks)
        self.assertEqual(result["stored_events"], ticks + 1)
        return result

    def open_archived(self):
        return Aquarium(self.db, SEED, GRAPH, exploration_mode="goal_free",
                        developmental_policy=ARCHIVE_MODE)

    def test_cross_source_cold_replay_and_exact_immutable_rows(self):
        origin = self.create_source_a(20)
        before = rows_fingerprint(self.db)
        with self.open_archived() as archived:
            self.assertEqual(archived.tick, origin["tick"])
            self.assertEqual(archived.checkpoint()["journal_head"], origin["journal_head"])
            self.assertEqual(archived.checkpoint()["developmental_state"]["schema"],
                             V2A_SCHEMA)
            self.assertEqual(archived.manifest["motor_policy"], "local_cell_v2")
            self.assertNotIn("developmental_policy", archived.manifest)
            self.assertEqual(archived.store._db.execute(
                "PRAGMA query_only").fetchone()[0], 1)
            self.assertEqual(archived.store.count(), before[0])
        self.assertEqual(rows_fingerprint(self.db), before)
        cli, err = run_cli(ROOT, self.db, policy=ARCHIVE_MODE, ticks=0)
        self.assertIsNotNone(cli, err[-900:])
        self.assertEqual(cli["journal_head"], before[2])
        self.assertEqual(rows_fingerprint(self.db), before)
        # The original writer remains authoritative and may append normally.
        newer, err = run_cli(
            PINNED_SOURCE, self.db, policy=ARCHIVE_MODE, ticks=1)
        self.assertIsNotNone(newer, err[-900:])
        self.assertEqual(newer["tick"], 21)
        with self.open_archived() as archived:
            self.assertEqual(archived.tick, 21)
            self.assertEqual(archived.checkpoint()["journal_head"], newer["journal_head"])

    def test_archive_rejects_step_gene_hmac_and_cli_writes(self):
        self.create_source_a()
        before = rows_fingerprint(self.db)
        with self.open_archived() as archived:
            with self.assertRaisesRegex(AquariumError, "strictly read-only"):
                archived.step()
            with self.assertRaisesRegex(AquariumError, "strictly read-only"):
                archived.acquire({"event_id": "forged"})
            with self.assertRaisesRegex(AquariumError, "cannot create a replay"):
                archived.save_replay_snapshot()
            with self.assertRaisesRegex(NoosphereError, "read-only archive"):
                archived.store.append({}, expected_head=archived.store.head())
        self.assertEqual(rows_fingerprint(self.db), before)
        failed, err = run_cli(ROOT, self.db, policy=ARCHIVE_MODE, ticks=1)
        self.assertIsNone(failed)
        self.assertIn("--ticks 0 only", err)
        self.assertEqual(rows_fingerprint(self.db), before)
        # Existing B-policy runtimes must not silently interpret v2A history.
        for mode in ("local_cell_v2", "local_cell_v3"):
            with self.subTest(mode=mode):
                bad, err = run_cli(ROOT, self.db, policy=mode, ticks=0)
                self.assertIsNone(bad)
                self.assertIn("mismatch", err)
                self.assertEqual(rows_fingerprint(self.db), before)

    def test_noncanonical_stored_event_rejected_without_adoption(self):
        self.create_source_a()
        original = rows_fingerprint(self.db)
        dirty = self.root / "tampered.sqlite3"
        with sqlite3.connect(self.db.resolve().as_uri() + "?mode=ro", uri=True) as src:
            with sqlite3.connect(dirty) as dest:
                src.backup(dest)
        with sqlite3.connect(dirty) as cx:
            cx.execute("DROP TRIGGER noosphere_no_update")
            wire = cx.execute("SELECT wire_json FROM events WHERE seq=1").fetchone()[0]
            cx.execute(
                "UPDATE events SET wire_json=? WHERE seq=1",
                ('{"event_id":"forged-first",' + wire[1:],),
            )
        with self.assertRaisesRegex(NoosphereError, "noncanonical stored JSON"):
            with Aquarium(dirty, SEED, GRAPH, exploration_mode="goal_free",
                          developmental_policy=ARCHIVE_MODE):
                pass
        self.assertEqual(rows_fingerprint(self.db), original)

    def test_invalid_archive_no_source_file_no_creation(self):
        absent = self.root / "absent.sqlite3"
        with self.assertRaisesRegex(AquariumError, "requires existing"):
            with Aquarium(absent, SEED, GRAPH, exploration_mode="goal_free",
                          developmental_policy=ARCHIVE_MODE):
                pass
        self.assertFalse(absent.exists())


if __name__ == "__main__":
    unittest.main()
