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

    def test_group_or_world_writable_directory_fails_before_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            unsafe = Path(folder) / "shared"
            unsafe.mkdir()
            os.chmod(unsafe, 0o777)
            path = unsafe / "tasks.sqlite3"
            with self.assertRaisesRegex(PermissionError, "parent directory"):
                coordinator.connect(str(path))
            self.assertFalse(path.exists())

    def test_symlinked_parent_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            actual = Path(folder) / "actual"
            actual.mkdir(mode=0o700)
            alias = Path(folder) / "alias"
            alias.symlink_to(actual, target_is_directory=True)
            path = alias / "tasks.sqlite3"
            with self.assertRaisesRegex(PermissionError, "parent directory"):
                coordinator.connect(str(path))
            self.assertFalse((actual / "tasks.sqlite3").exists())

    def test_writable_ancestor_rejected_even_with_private_immediate_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            shared = Path(folder) / "untrusted"
            shared.mkdir()
            os.chmod(shared, 0o777)
            private = shared / "private"
            private.mkdir(mode=0o700)
            path = private / "tasks.sqlite3"
            with self.assertRaisesRegex(PermissionError, "parent directory"):
                coordinator.connect(str(path))
            self.assertFalse(path.exists())

    def test_symlinked_ancestor_rejected_even_if_immediate_parent_is_regular(self):
        with tempfile.TemporaryDirectory() as folder:
            real = Path(folder) / "real"
            real.mkdir(mode=0o700)
            private = real / "private"
            private.mkdir(mode=0o700)
            alias = Path(folder) / "alias"
            alias.symlink_to(real, target_is_directory=True)
            path = alias / "private" / "tasks.sqlite3"
            with self.assertRaisesRegex(PermissionError, "parent directory"):
                coordinator.connect(str(path))
            self.assertFalse((private / "tasks.sqlite3").exists())

    def test_symlink_dotdot_path_cannot_bypass_parent_preflight(self):
        with tempfile.TemporaryDirectory() as folder:
            safe = Path(folder) / "safe"
            safe.mkdir(mode=0o700)
            (safe / "target").mkdir(mode=0o700)
            other = Path(folder) / "other"
            other.mkdir(mode=0o700)
            (other / "sub").mkdir(mode=0o700)
            actual = other / "target"
            actual.mkdir(mode=0o777)
            os.chmod(actual, 0o777)
            (safe / "link").symlink_to(other / "sub", target_is_directory=True)
            # abspath sees safe/target; the filesystem sees other/target.
            disguised = safe / "link" / ".." / "target" / "tasks.sqlite3"
            self.assertNotEqual(os.path.abspath(disguised),
                                os.path.realpath(disguised))
            with self.assertRaisesRegex(PermissionError, "parent directory"):
                coordinator.connect(str(disguised))
            self.assertFalse((actual / "tasks.sqlite3").exists())

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
