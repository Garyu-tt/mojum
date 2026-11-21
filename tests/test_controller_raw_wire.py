"""Strict raw SQLite wire regression for the opt-in synthetic controller.

All corruption is performed on a disposable local test journal. Canonical
logical event comparison alone cannot detect extraneous raw JSON whitespace
(or duplicated JSON members) if the parsed event and its hash remain equal.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.noosphere import LocalNoosphere, NoosphereError

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class ControllerRawWireTests(unittest.TestCase):
    def test_noncanonical_raw_frame_rejected_with_unchanged_event_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "controller.sqlite3"
            opts = dict(neural_mode="disabled", motor_mode="developmental_fixture",
                        brain_mode="developmental_controller")
            with Aquarium(db_path, SEED, GRAPH, **opts) as aquarium:
                aquarium.step()
                saved_head = aquarium.checkpoint()["journal_head"]
                saved_count = aquarium.store.count()

            with closing(sqlite3.connect(db_path)) as db:
                row = db.execute(
                    "SELECT seq,wire_json,event_hash FROM events "
                    "WHERE topic='aquarium.frame'"
                ).fetchone()
                self.assertIsNotNone(row)
                seq, wire, event_hash = row
                # Disposable QA-only mutation: the event's parsed value and
                # committed SHA do not change, but its stored bytes do.
                db.execute("DROP TRIGGER noosphere_no_update")
                db.execute("UPDATE events SET wire_json=? WHERE seq=?",
                           (wire + " ", seq))
                db.commit()

            # Ordinary chain/hash verification still accepts the altered raw
            # representation, demonstrating why the stronger guard matters.
            with LocalNoosphere(db_path, "dante-fixture-001") as journal:
                self.assertEqual(journal.verify_chain(strict_wire=False)["head"], saved_head)
                with self.assertRaisesRegex(NoosphereError, "noncanonical event"):
                    journal.verify_chain(strict_wire=True)
                self.assertEqual(journal.count(), saved_count)
                self.assertEqual(journal.head(), event_hash)

            # The opt-in controller must always apply that stronger guard.
            with self.assertRaisesRegex(NoosphereError, "noncanonical event"):
                with Aquarium(db_path, SEED, GRAPH, **opts):
                    pass


if __name__ == "__main__":
    unittest.main()
