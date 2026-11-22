"""POSIX file privacy and fail-closed storage regressions for Spartan budget.

These fixtures never call a provider, run a model, or use real credentials.
"""
from contextlib import closing
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import spartan_budget as budget


@unittest.skipUnless(os.name == "posix", "requires POSIX file mode and ownership")
class PrivateLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "ledger.sqlite3"

    def test_mode_0600_under_umask_022_including_wal_and_shm(self):
        previous = os.umask(0o022)
        try:
            with closing(budget.connect(self.path)) as db:
                budget.schema(db)
                budget.configure(db, 100, 100)
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO requests VALUES (?, ?, ?, ?, 'reserved', NULL, NULL)",
                           ("entry", 1, 1, 1))
                for suffix in ("", "-wal", "-shm"):
                    file = Path(str(self.path) + suffix)
                    info = file.lstat()
                    self.assertTrue(stat.S_ISREG(info.st_mode), suffix)
                    self.assertEqual(stat.S_IMODE(info.st_mode), 0o600, suffix)
                    self.assertEqual(info.st_uid, os.geteuid(), suffix)
                    self.assertEqual(info.st_nlink, 1, suffix)
                db.execute("ROLLBACK")
        finally:
            os.umask(previous)

    def test_reject_existing_world_readable_database_without_chmod(self):
        self.path.write_bytes(b"")
        self.path.chmod(0o644)
        with self.assertRaisesRegex(budget.BudgetError, "not owner-private"):
            budget.connect(self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o644)

    def test_reject_group_readable_or_symlinked_sidecars_before_open(self):
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(self.path) + suffix)
                sidecar.write_bytes(b"reserved private evidence")
                sidecar.chmod(0o640)
                with self.assertRaisesRegex(budget.BudgetError, "not owner-private"):
                    budget.connect(self.path)
                self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o640)
                self.assertFalse(self.path.exists())
                sidecar.unlink()

        victim = self.root / "victim"
        victim.write_text("PRIVATE", encoding="utf-8")
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.path) + suffix)
            with self.subTest(symlink=suffix):
                sidecar.symlink_to(victim)
                with self.assertRaisesRegex(budget.BudgetError, "not owner-private"):
                    budget.connect(self.path)
                self.assertFalse(self.path.exists())
                self.assertEqual(victim.read_text(encoding="utf-8"), "PRIVATE")
                sidecar.unlink()

    def test_reject_symlinked_database_and_nonregular_inode(self):
        original = self.root / "original"
        original.write_bytes(b"")
        self.path.symlink_to(original)
        with self.assertRaisesRegex(budget.BudgetError, "not owner-private"):
            budget.connect(self.path)
        self.path.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.path)
            with self.assertRaisesRegex(budget.BudgetError, "not owner-private"):
                budget.connect(self.path)

    def test_reject_shared_leaf_and_replaceable_ancestors(self):
        shared = self.root / "public"
        shared.mkdir(mode=0o755)
        with self.assertRaisesRegex(budget.BudgetError, "not owner-private"):
            budget.connect(shared / "budget.sqlite3")
        self.assertFalse((shared / "budget.sqlite3").exists())
        shared.chmod(0o777)
        private = shared / "private"
        private.mkdir(mode=0o700)
        with self.assertRaisesRegex(budget.BudgetError, "not owner-private"):
            budget.connect(private / "budget.sqlite3")
        self.assertFalse((private / "budget.sqlite3").exists())

        real = self.root / "real"
        real.mkdir(mode=0o700)
        alias = self.root / "alias"
        alias.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(budget.BudgetError, "symlink"):
            budget.connect(alias / "budget.sqlite3")
        self.assertFalse((real / "budget.sqlite3").exists())

    def test_relative_or_sqlite_uri_is_never_a_durable_ledger(self):
        for path in (":memory:", "file:ledger.sqlite3?mode=memory",
                     "ledger.sqlite3", "./ledger.sqlite3",
                     str(self.root / ".." / "other.sqlite3")):
            with self.subTest(path=path), self.assertRaisesRegex(
                budget.BudgetError, "absolute private filesystem path"
            ):
                budget.connect(path)

    def test_safe_reopen_does_not_create_duplicate_reservation(self):
        with closing(budget.connect(self.path)) as db:
            budget.schema(db)
            budget.configure(db, 10, 0)
            self.assertTrue(budget.reserve(db, "r", 1, 2, 0)["dispatch_allowed"])
        with closing(budget.connect(self.path)) as db:
            self.assertFalse(budget.reserve(db, "r", 1, 2, 0)["dispatch_allowed"])
            self.assertEqual(budget.status(db)["debited_tokens"], 2)

    def test_cli_denies_unsafe_database_without_traceback(self):
        shared = self.root / "public"
        shared.mkdir(mode=0o755)
        filename = shared / "leaky.sqlite3"
        result = subprocess.run([
            sys.executable, str(ROOT / "tools" / "spartan_budget.py"),
            "--db", str(filename), "init", "--tokens", "10",
            "--micro-usd", "0"
        ], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("budget denied:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(filename.exists())


if __name__ == "__main__":
    unittest.main()
