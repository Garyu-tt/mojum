"""Spartan task prompts and WAL files must never be world-readable."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import spartan_coordinator as coordinator


@unittest.skipUnless(os.name == "posix", "POSIX owner/mode contract")
class SpartanPrivateDatabaseTests(unittest.TestCase):
    def test_new_database_and_wal_are_owner_only_even_with_umask_022(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.sqlite3"
            previous = os.umask(0o022)
            try:
                with closing(coordinator.connect(str(path))) as db:
                    coordinator.create_schema(db)
                    coordinator.init(db, 300, 100)
                    coordinator.enqueue(db, [{
                        "id": "private-task", "scope": "private.module",
                        "payload": {"prompt": "secret synthetic test prompt"},
                    }], 100)
                    for suffix in ("", "-wal", "-shm"):
                        item = Path(str(path) + suffix)
                        self.assertTrue(item.is_file(), suffix)
                        self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600, suffix)
                # Opening the database must not mutate a process-global umask.
                self.assertEqual(os.umask(0o022), 0o022)
            finally:
                os.umask(previous)

    def test_existing_world_readable_database_is_rejected_without_chmod(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE example(x)")
                db.commit()
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(PermissionError, "owner-only"):
                coordinator.connect(str(path))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_existing_insecure_sidecar_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.sqlite3"
            with closing(coordinator.connect(str(path))) as db:
                coordinator.create_schema(db)
                coordinator.init(db, 1, 100)
                sidecar = Path(str(path) + "-wal")
                self.assertTrue(sidecar.exists())
                os.chmod(sidecar, 0o644)
                with self.assertRaisesRegex(PermissionError, "owner-only"):
                    coordinator.connect(str(path))

    def test_symlink_does_not_redirect_private_database(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "target.sqlite3"
            with closing(coordinator.connect(str(target))) as db:
                coordinator.create_schema(db)
            link = Path(folder) / "alias.sqlite3"
            link.symlink_to(target)
            with self.assertRaisesRegex(PermissionError, "owner-only"):
                coordinator.connect(str(link))


if __name__ == "__main__":
    unittest.main()
