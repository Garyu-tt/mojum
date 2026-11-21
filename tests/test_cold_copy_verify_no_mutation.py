"""QA: verification of a private SQLite backup must never rewrite its input.

The Aquarium constructor auto-bootstraps an empty journal, so a verify API
must isolate SQLite open/replay side effects from the original archive.
"""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.aquarium_cold_copy import export_cold_copy, verify_cold_copy


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
ID = SEED["genome_id"]


class ReadOnlyVerifyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "source.sqlite3"
        self.archive = self.root / "archive.sqlite3"

    def assert_no_new_archive_sidecars(self):
        self.assertFalse(Path(str(self.archive) + "-wal").exists())
        self.assertFalse(Path(str(self.archive) + "-shm").exists())

    def test_export_rejects_damaged_source_metadata_without_repair(self):
        # Source has committed Aquarium events but an invalid metadata table.
        # Export must not recreate it before replaying the source.
        for mutation in ("empty", "dropped", "foreign_owner"):
            with self.subTest(mutation=mutation):
                source = self.root / ("source-" + mutation + ".sqlite3")
                with Aquarium(source, SEED, GRAPH) as aquarium:
                    aquarium.step()
                with closing(sqlite3.connect(source)) as connection:
                    if mutation == "empty":
                        connection.execute("DELETE FROM metadata")
                    elif mutation == "dropped":
                        connection.execute("DROP TABLE metadata")
                    else:
                        connection.execute(
                            "UPDATE metadata SET v=? WHERE k='owner'", ("foreign-owner",)
                        )
                    connection.commit()
                before = source.read_bytes()
                with self.assertRaisesRegex(ValueError, "metadata"):
                    export_cold_copy(source, self.archive, SEED, GRAPH)
                self.assertEqual(source.read_bytes(), before)
                self.assertFalse(self.archive.exists())

    def test_empty_existing_sqlite_is_rejected_without_auto_bootstrap(self):
        # Reproduces the old behavior: Aquarium(path, ...) opened the ORIGINAL
        # archive and appended aquarium.bootstrap before rejecting pinned head.
        with closing(sqlite3.connect(self.archive)) as connection:
            # A nonempty, valid SQLite file with no Aquarium events.
            connection.execute("CREATE TABLE fixture_marker (id INTEGER)")
            connection.commit()
        original = self.archive.read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        with self.assertRaises(ValueError):
            verify_cold_copy(
                self.archive, SEED, GRAPH, expected_agent_id=ID,
                expected_journal_head="0" * 64,
                expected_sha256=digest, expected_tick=0,
            )
        self.assertEqual(self.archive.read_bytes(), original)
        self.assert_no_new_archive_sidecars()

    def test_successful_verification_does_not_modify_archive(self):
        with Aquarium(self.source, SEED, GRAPH) as baby:
            for _ in range(4):
                baby.step()
        meta = export_cold_copy(self.source, self.archive, SEED, GRAPH)
        original = self.archive.read_bytes()
        result = verify_cold_copy(
            self.archive, SEED, GRAPH,
            expected_agent_id=ID,
            expected_journal_head=meta["journal_head"],
            expected_sha256=meta["db_sha256"],
            expected_tick=4,
        )
        self.assertEqual(result["tick"], 4)
        self.assertEqual(result["db_sha256_before_replay"], meta["db_sha256"])
        self.assertEqual(self.archive.read_bytes(), original)
        self.assert_no_new_archive_sidecars()

    def test_wrong_expected_head_does_not_modify_archive(self):
        with Aquarium(self.source, SEED, GRAPH) as baby:
            baby.step()
        meta = export_cold_copy(self.source, self.archive, SEED, GRAPH)
        original = self.archive.read_bytes()
        with self.assertRaises(ValueError):
            verify_cold_copy(
                self.archive, SEED, GRAPH, expected_agent_id=ID,
                expected_journal_head="0" * 64,
                expected_sha256=meta["db_sha256"],
                expected_tick=1,
            )
        self.assertEqual(self.archive.read_bytes(), original)
        self.assert_no_new_archive_sidecars()

    def test_non_self_contained_sqlite_sidecar_is_rejected_unchanged(self):
        with Aquarium(self.source, SEED, GRAPH) as baby:
            baby.step()
        meta = export_cold_copy(self.source, self.archive, SEED, GRAPH)
        original = self.archive.read_bytes()
        sidecar = Path(str(self.archive) + "-wal")
        sidecar.write_bytes(b"not a real WAL")
        try:
            with self.assertRaisesRegex(ValueError, "self-contained"):
                verify_cold_copy(
                    self.archive, SEED, GRAPH, expected_agent_id=ID,
                    expected_journal_head=meta["journal_head"],
                    expected_sha256=meta["db_sha256"],
                )
            self.assertEqual(self.archive.read_bytes(), original)
            self.assertEqual(sidecar.read_bytes(), b"not a real WAL")
        finally:
            sidecar.unlink()


if __name__ == "__main__":
    unittest.main()
