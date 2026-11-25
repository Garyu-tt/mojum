"""The backup page budget applies to the actual replay snapshot."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mojum import aquarium_cold_copy, aquarium_trace_export
from mojum.aquarium import Aquarium


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class AquariumBackupSnapshotTests(unittest.TestCase):
    def test_concurrent_growth_does_not_change_the_admitted_backup(self):
        real_connect = sqlite3.connect
        exporters = (
            (aquarium_cold_copy, "MAX_BACKUP_BYTES", aquarium_cold_copy.export_cold_copy, {}),
            (
                aquarium_trace_export, "MAX_SOURCE_BYTES",
                aquarium_trace_export.export_private_trace, {"allow_private_export": True},
            ),
        )
        for module, limit, export, options in exporters:
            with self.subTest(export=export.__name__), tempfile.TemporaryDirectory() as scratch:
                source = Path(scratch).resolve() / "source.sqlite3"
                destination = source.with_name("export")
                with Aquarium(source, SEED, GRAPH) as aquarium, closing(real_connect(source)) as writer:
                    aquarium.step()
                    original_head = aquarium.checkpoint()["journal_head"]
                    page_size = writer.execute("PRAGMA page_size").fetchone()[0]
                    budget = writer.execute("PRAGMA page_count").fetchone()[0] * page_size

                    class RacingConnection(sqlite3.Connection):
                        def backup(self, target, **kwargs):
                            # Commit through other WAL connections after the
                            # exporter checks its budget and before SQLite copies.
                            writer.execute("CREATE TABLE growth (data BLOB)")
                            writer.execute("INSERT INTO growth VALUES (zeroblob(?))", (budget * 2,))
                            writer.commit()
                            aquarium.step()
                            return super().backup(target, **kwargs)

                    def connect(path, *args, **kwargs):
                        if path == source.as_uri() + "?mode=ro":
                            kwargs["factory"] = RacingConnection
                        return real_connect(path, *args, **kwargs)

                    with patch.object(sqlite3, "connect", connect), patch.object(module, limit, budget):
                        result = export(source, destination, SEED, GRAPH, **options)

                    current_pages = writer.execute("PRAGMA page_count").fetchone()[0]
                    self.assertGreater(current_pages * page_size, budget)
                    self.assertNotEqual(aquarium.checkpoint()["journal_head"], original_head)
                    self.assertEqual(result["journal_head"], original_head)
                    self.assertTrue(destination.is_file())
