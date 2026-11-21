"""Acceptance QA: v4 read-only wire inspection rejects forged bootstrap clocks.

The journal itself remains a real, private, disposable SQLite hash chain.
No production Noosphere history is modified.
"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium, V4_MODE
from mojum.aquarium_wire import inspect_journal
from mojum.noosphere import EMPTY_HEAD, LocalNoosphere, NoosphereError


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class V4BootstrapClockWireTests(unittest.TestCase):
    def test_real_valid_bootstrap_is_still_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "valid.sqlite3"
            with Aquarium(
                db, SEED, GRAPH, motor_mode=V4_MODE, neural_mode="disabled"
            ):
                pass
            report = inspect_journal(db)
            self.assertEqual(report["format"], V4_MODE)
            self.assertEqual(report["frames"], 0)
            self.assertEqual(report["events"], 1)

    def test_forged_bootstrap_dt_rejected_with_valid_sqlite_hash_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.sqlite3"
            with Aquarium(
                source, SEED, GRAPH, motor_mode=V4_MODE, neural_mode="disabled"
            ) as aquarium:
                bootstrap = deepcopy(next(
                    aquarium.store.iter_events(include_nonpublic=True)
                )["event"])

            for label, value in (
                ("missing", None),
                ("zero", 0),
                ("below-minimum", 999),
                ("above-maximum", 1_000_000_001),
                ("boolean", True),
                ("string", "10000000"),
            ):
                with self.subTest(case=label):
                    db = Path(tmp) / (label + ".sqlite3")
                    forged = deepcopy(bootstrap)
                    if label == "missing":
                        del forged["payload"]["dt_ns"]
                    else:
                        forged["payload"]["dt_ns"] = value
                    with LocalNoosphere(db, forged["agent_id"]) as store:
                        store.append(forged, expected_head=EMPTY_HEAD)
                        original = store.verify_chain()
                        self.assertEqual(original["count"], 1)
                    with self.assertRaisesRegex(
                        NoosphereError, "invalid v4 bootstrap dt_ns"
                    ):
                        inspect_journal(db)
                    with LocalNoosphere(
                        db, forged["agent_id"], read_only=True
                    ) as store:
                        self.assertEqual(store.verify_chain(), original)


if __name__ == "__main__":
    unittest.main()
