"""Opt-in real Litestream v0.5.18 smoke; skipped without pinned native binary.

PUBLIC_SYNTHETIC disposable files only. This is not an independent trusted
Noosphere anchor, production reliability or performance certification.
"""
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import litestream_local as mod


@unittest.skipUnless(os.name == "posix" and os.getenv("LITESTREAM_BINARY"),
                     "real Litestream native binary not provisioned")
class NativeLitestreamSmoke(unittest.TestCase):
    def test_native_wal_commit_then_two_restores(self):
        binary = os.environ["LITESTREAM_BINARY"]
        mod.verify_version(binary)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / "private"
            replica = root / "replica"
            private.mkdir(mode=0o700)
            replica.mkdir(mode=0o700)
            source = private / "source.db"
            with sqlite3.connect(source) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE sample (value INTEGER NOT NULL)")
                db.execute("INSERT INTO sample VALUES (1)")
            source.chmod(0o600)
            failures = []

            def write_during_replication():
                try:
                    time.sleep(2)
                    with sqlite3.connect(source) as db:
                        db.execute("INSERT INTO sample VALUES (2)")
                except Exception as exc:
                    failures.append(str(exc))

            writer = threading.Thread(target=write_during_replication)
            writer.start()
            try:
                mod.replicate_for(binary, str(source), str(replica), seconds=8)
            finally:
                writer.join(timeout=5)
            self.assertFalse(failures, "native synthetic SQLite writer failed")
            self.assertFalse(writer.is_alive(), "native synthetic writer hung")
            expected = private / "first-native-restore.db"
            result = subprocess.run([binary, "restore", "-integrity-check", "full",
                                     "-o", str(expected), mod._file_url(replica)],
                                    stdin=subprocess.DEVNULL, capture_output=True,
                                    timeout=25, check=False)
            self.assertEqual(result.returncode, 0, "native Litestream restore failed")
            with sqlite3.connect(expected) as db:
                self.assertEqual(db.execute("SELECT value FROM sample ORDER BY value").fetchall(),
                                 [(1,), (2,)])
            # First-restore SHA is a disposable smoke comparator,
            # NOT an independently verified production authenticity anchor.
            digest = hashlib.sha256(expected.read_bytes()).hexdigest()
            candidate = private / "restored-by-wrapper.db"
            mod.restore_verified(binary, str(replica), str(candidate),
                                 expected_sha256=digest, timeout=30)
            self.assertEqual(candidate.read_bytes(), expected.read_bytes())


if __name__ == "__main__":
    unittest.main()
