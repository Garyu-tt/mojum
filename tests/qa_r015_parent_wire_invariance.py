"""Independent pinned-parent-to-candidate wire ABI regression.

Run CLI from two independent source trees in separate subprocesses, then
compare the full persisted SQLite records and checkpoint JSON exactly.
Never access real organisms, network services or non-temporary journals.
"""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(os.environ["MOJUM_BASELINE135_SOURCE"]).resolve() if os.environ.get(
    "MOJUM_BASELINE135_SOURCE") else None


def _run(source, database, args):
    env = os.environ.copy()
    env["PYTHONPATH"] = str((source / "src").resolve())
    cmd = [
        sys.executable, "-m", "mojum.aquarium", "--db", str(database),
        "--genome", str(source / "examples" / "genesis_seed.json"),
        "--graph", str(source / "examples" / "first_loop.json"),
        "--ticks", "11", *args,
    ]
    process = subprocess.run(
        cmd, cwd=source, env=env, capture_output=True, text=True,
        timeout=30, check=False)
    if process.returncode:
        raise AssertionError("Aquarium CLI failed: " + process.stderr[-800:])
    return json.loads(process.stdout)


def _saved_wire(database):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro",
                                 uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        meta = db.execute("SELECT k,v FROM metadata ORDER BY k").fetchall()
        events = db.execute(
            "SELECT seq,event_id,agent_id,recorded_at_ns,observed_at_ns,"
            "valid_from_ns,valid_to_ns,topic,visibility,wire_json,prev_hash,"
            "event_hash FROM events ORDER BY seq"
        ).fetchall()
    return meta, events


@unittest.skipUnless(BASELINE is not None,
                     "requires pinned parent #135 checkout for byte-level regression")
class ParentWireInvariance(unittest.TestCase):
    def test_legacy_default_unchanged_against_exact_parent_135(self):
        self.assertTrue((BASELINE / "src" / "mojum" / "aquarium.py").is_file())
        cases = {
            "scripted": [],
            "goal_free_legacy": ["--exploration-mode", "goal_free"],
            "v2b": ["--exploration-mode", "goal_free",
                    "--developmental-policy", "local_cell_v2"],
            "v3b": ["--exploration-mode", "goal_free",
                    "--developmental-policy", "local_cell_v3"],
            "toy_probe": ["--exploration-mode", "goal_free",
                          "--sensory-mode", "toy_probe"],
        }
        for label, arguments in cases.items():
            with self.subTest(mode=label):
                with tempfile.TemporaryDirectory(prefix="mojum-r015-wire-") as tmp:
                    old_db = Path(tmp) / "old-private.sqlite3"
                    new_db = Path(tmp) / "candidate-private.sqlite3"
                    old_checkpoint = _run(BASELINE, old_db, arguments)
                    # The immutable parent still uses the historical spelling.
                    updated = [arg if arg != "local_cell_v2" else "local_cell_v2b"
                               for arg in arguments]
                    new_checkpoint = _run(ROOT, new_db, updated)
                    old_meta, old_events = _saved_wire(old_db)
                    new_meta, new_events = _saved_wire(new_db)
                    self.assertEqual(old_meta, new_meta)
                    self.assertEqual(old_events, new_events)
                    self.assertEqual(old_checkpoint, new_checkpoint)
                    self.assertEqual(len(old_events), 12)
                    self.assertEqual(json.loads(new_events[0][9])["payload"]["schema"],
                                     "mojum.aquarium.bootstrap/1")
                    self.assertNotIn("room_exact",
                                     json.loads(new_events[0][9])["payload"])


if __name__ == "__main__":
    unittest.main()
