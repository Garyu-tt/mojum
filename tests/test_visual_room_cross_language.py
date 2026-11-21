"""Visual room JS export -> original Python parser -> real synthetic SQLite.

No subprocess/browser network, no installs, only local disposable test journals.
Node is installed by the dedicated workflow. Browser rendering is *not*
claimed by this particular cross-language test.
"""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from mojum.room_input import read_new_room_contract
from mojum.world_room import _revision

ROOT = Path(__file__).resolve().parents[1]
EXPERT = ROOT / "examples/room_exact_mixed.json"
SIMPLE = ROOT / "examples/room_simple_mixed.json"


def _node_json():
    expression = (
        "const C=require('./tools/room_designer_core.js');"
        "process.stdout.write(C.draftJSON(C.clone(C.DEFAULT)));"
    )
    return subprocess.run(
        ["node", "-e", expression],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=15,
    ).stdout


def _run_room(db, config):
    return subprocess.run(
        [
            sys.executable, "-m", "mojum.aquarium",
            "--db", str(db),
            "--exploration-mode", "goal_free",
            "--developmental-policy", "local_cell_v2",
            "--room-numeric-contract", "typed_v1",
            "--new-room-contract", str(config),
            "--ticks", "18",
        ],
        cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        capture_output=True, text=True, timeout=35,
    )


def _wire(db):
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
        con.execute("PRAGMA query_only=ON")
        return con.execute(
            "SELECT seq, wire_json, event_hash FROM events ORDER BY seq"
        ).fetchall()


@unittest.skipUnless(shutil.which("node"), "requires Node 22 for JS contract exporter")
class BrowserDraftPythonCompatibilityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mojum-visual-room-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.input = self.root / "visual-export.json"
        self.input.write_text(_node_json(), encoding="utf-8")

    def test_js_export_exactly_recovers_types_and_python_room_revision(self):
        exported = self.input.read_text(encoding="utf-8")
        self.assertIn('"height": 4,', exported)
        self.assertIn('[2.0, 1, 0.125, 2.0]', exported)
        self.assertEqual(
            json.loads(exported),
            json.loads(SIMPLE.read_text(encoding="utf-8")),
        )
        a, start_a = read_new_room_contract(self.input)
        b, start_b = read_new_room_contract(EXPERT)
        self.assertEqual(type(a.width), float)
        self.assertEqual(type(a.height), int)
        self.assertEqual([type(x) for x in a.obstacles[0]],
                         [float, int, float, float])
        self.assertEqual(start_a, start_b)
        self.assertEqual(
            _revision(a, 0, *start_a),
            _revision(b, 0, *start_b),
        )

    def test_js_export_and_original_example_produce_identical_journal_events(self):
        generated_db = self.root / "generated.sqlite3"
        reference_db = self.root / "reference.sqlite3"
        generated = _run_room(generated_db, self.input)
        reference = _run_room(reference_db, EXPERT)
        self.assertEqual(generated.returncode, 0, generated.stderr[-900:])
        self.assertEqual(reference.returncode, 0, reference.stderr[-900:])
        self.assertEqual(_wire(generated_db), _wire(reference_db))
        self.assertEqual(json.loads(generated.stdout), json.loads(reference.stdout))
        events = _wire(generated_db)
        self.assertEqual(len(events), 19)
        self.assertEqual(
            json.loads(events[0][1])["payload"]["schema"],
            "mojum.aquarium.bootstrap/2",
        )
        self.assertNotIn("point-room-draft", events[0][1])

    def test_invalid_editor_input_is_refused_without_source_writes(self):
        invalid = self.root / "invalid.json"
        invalid.write_text('{"schema":"mojum.world.point-room-draft/1",'
                           '"width":4,"height":4,"obstacles":[[2,1,1,1]],'
                           '"start":[2.5,1.5]}', encoding="utf-8")
        db = self.root / "refused.sqlite3"
        command = _run_room(db, invalid)
        self.assertNotEqual(command.returncode, 0)
        self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
