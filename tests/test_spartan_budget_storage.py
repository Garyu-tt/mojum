"""Independent POSIX private-storage acceptance for the paid-model budget ledger."""

from contextlib import closing
import os
from pathlib import Path
from types import SimpleNamespace
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from mojum.spartan_budget import BudgetCaps, BudgetDenied, BudgetLedger


@unittest.skipUnless(os.name == "posix", "POSIX-only filesystem security")
class BudgetPrivateStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.caps = BudgetCaps(100, 10000, 10000)

    def test_umask_022_does_not_expose_database_or_sidecars(self):
        path = self.root / "ledger.sqlite3"
        old_umask = os.umask(0o022)
        try:
            ledger = BudgetLedger(path, self.caps)
            ledger.reserve("task-1", "model-a", 20, 100, now_s=100)
            with closing(ledger._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE budget_settings SET last_epoch_s=100")
                for suffix in ("", "-wal", "-shm"):
                    item = Path(str(path) + suffix)
                    info = item.lstat()
                    self.assertTrue(stat.S_ISREG(info.st_mode))
                    self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
                    self.assertEqual(info.st_uid, os.getuid())
                    self.assertEqual(info.st_nlink, 1)
                db.rollback()
        finally:
            os.umask(old_umask)

    def test_insecure_existing_database_fails_closed(self):
        path = self.root / "insecure.sqlite3"
        path.write_bytes(b"")
        path.chmod(0o644)
        with self.assertRaisesRegex(BudgetDenied, "not private"):
            BudgetLedger(path, self.caps)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_symlink_and_group_readable_sidecar_fail_closed(self):
        target = self.root / "target"
        target.write_bytes(b"")
        link = self.root / "symlink.sqlite3"
        link.symlink_to(target)
        with self.assertRaises(BudgetDenied):
            BudgetLedger(link, self.caps)

        path = self.root / "sidecar.sqlite3"
        companion = Path(str(path) + "-wal")
        companion.write_bytes(b"")
        companion.chmod(0o644)
        with self.assertRaisesRegex(BudgetDenied, "not private"):
            BudgetLedger(path, self.caps)
        self.assertEqual(stat.S_IMODE(companion.stat().st_mode), 0o644)

    def test_parent_must_be_private_with_nonreplaceable_ancestors(self):
        shared = self.root / "shared"
        shared.mkdir(mode=0o755)
        with self.assertRaisesRegex(BudgetDenied, "mode 0700"):
            BudgetLedger(shared / "ledger.sqlite3", self.caps)
        shared.chmod(0o700)
        BudgetLedger(shared / "ledger.sqlite3", self.caps)

        world = self.root / "world"
        world.mkdir(mode=0o777)
        world.chmod(0o777)
        private = world / "private"
        private.mkdir(mode=0o700)
        with self.assertRaisesRegex(BudgetDenied, "replaceable ancestor"):
            BudgetLedger(private / "ledger.sqlite3", self.caps)

    def test_foreign_owned_ancestor_rejected_even_if_permissions_look_safe(self):
        # A different UID owning a 0755 parent may rename our private child.
        # A sticky 01777 parent is also unsafe when owned by that other UID.
        foreign = self.root / "foreign-ancestor"
        foreign.mkdir(mode=0o755)
        private = foreign / "private"
        private.mkdir(mode=0o700)
        target = private / "ledger.sqlite3"
        real_lstat = Path.lstat

        def simulate_foreign_owner(path):
            info = real_lstat(path)
            if path == foreign:
                return SimpleNamespace(
                    st_mode=info.st_mode, st_uid=os.geteuid() + 100000
                )
            return info

        for permissions in (0o755, 0o1777):
            with self.subTest(permissions=permissions):
                foreign.chmod(permissions)
                with patch.object(Path, "lstat", simulate_foreign_owner):
                    with self.assertRaisesRegex(BudgetDenied, "replaceable ancestor"):
                        BudgetLedger(target, self.caps)
                self.assertFalse(target.exists())

    def test_concurrent_connections_preserve_private_files(self):
        path = self.root / "concurrent.sqlite3"
        BudgetLedger(path, self.caps)
        errors = []

        def run(i):
            try:
                ledger = BudgetLedger(path, self.caps)
                ledger.reserve(f"task-{i}", "model-a", 1, 1, now_s=1000)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=12)
        self.assertEqual(errors, [])
        for thread in threads:
            self.assertFalse(thread.is_alive())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
