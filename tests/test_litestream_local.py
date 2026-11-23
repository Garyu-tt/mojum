"""Offline regression tests; real Litestream is not required or claimed."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import litestream_local as mod


class LitestreamLocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.private = root / "private"
        self.private.mkdir(mode=0o700)
        self.replica = root / "replica"
        self.replica.mkdir(mode=0o700)
        self.source = self.private / "source.db"
        with sqlite3.connect(self.source) as db:
            db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, event TEXT NOT NULL)")
            db.execute("INSERT INTO events (event) VALUES ('public synthetic')")
        self.source.chmod(0o600)
        self.expected = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.binary = self.private / "fake-litestream"
        self._write_fake()

    def _write_fake(self, *, version=mod.PINNED_VERSION, code="normal"):
        script = f'''#!{sys.executable}
import shutil, sys, time
from pathlib import Path
if sys.argv[1:] == ["version"]:
    print("litestream v{version}")
    sys.exit(0)
if sys.argv[1] == "restore":
    if {code!r} == "failed": sys.exit(5)
    output = Path(sys.argv[sys.argv.index("-o") + 1])
    if {code!r} == "corrupt": output.write_bytes(b"x" * 1024)
    elif {code!r} != "missing": shutil.copyfile({str(self.source)!r}, output)
    if {code!r} == "sidecar": Path(str(output) + "-wal").write_bytes(b"unsafe")
    sys.exit(0)
if sys.argv[1] == "replicate":
    if {code!r} == "failed": sys.exit(3)
    time.sleep(30)
else: sys.exit(9)
'''
        self.binary.write_text(script)
        self.binary.chmod(0o700)

    def test_plan_accepts_private_valid_sqlite(self):
        db, replica = mod.replica_plan(str(self.source), str(self.replica))
        self.assertEqual(db, self.source)
        self.assertEqual(replica, self.replica)
        self.assertTrue(mod._file_url(Path("/tmp/a b#c")).endswith("/a%20b%23c"))

    def test_plan_never_writes_source_or_replica(self):
        before = self.source.read_bytes()
        mod.replica_plan(str(self.source), str(self.replica))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(list(self.replica.iterdir()), [])

    def test_path_traversal_relative_and_newline_rejected(self):
        for bad in (str(self.source.parent / ".." / "source.db"),
                    "relative.db", str(self.source) + "\n"):
            with self.subTest(bad=bad), self.assertRaises(mod.SafetyError):
                mod.replica_plan(bad, str(self.replica))

    def test_symlink_source_replica_and_target_rejected(self):
        link = self.private / "alias.db"
        link.symlink_to(self.source)
        with self.assertRaises(mod.SafetyError):
            mod.replica_plan(str(link), str(self.replica))
        link.unlink()
        link.symlink_to(self.replica, target_is_directory=True)
        with self.assertRaises(mod.SafetyError):
            mod.replica_plan(str(self.source), str(link))
        link.unlink()
        target = self.private / "restore.db"
        target.symlink_to(self.source)
        with self.assertRaises(mod.SafetyError):
            mod.restore_plan(str(self.replica), str(target))

    def test_reject_nonprivate_and_invalid_sqlite(self):
        self.source.chmod(0o644)
        with self.assertRaises(mod.SafetyError):
            mod.replica_plan(str(self.source), str(self.replica))
        self.source.chmod(0o600)
        self.source.write_bytes(b"not a sqlite file" * 20)
        with self.assertRaises(mod.SafetyError):
            mod.replica_plan(str(self.source), str(self.replica))
        self.replica.chmod(0o755)
        with self.assertRaises(mod.SafetyError):
            mod.restore_plan(str(self.replica), str(self.private / "out.db"))

    def test_version_pin_and_binary_digest(self):
        self.assertEqual(mod.verify_version(str(self.binary)), self.binary)
        with self.assertRaises(mod.SafetyError):
            mod.verify_version(str(self.binary), "0" * 64)
        self._write_fake(version="0.5.13")
        with self.assertRaises(mod.SafetyError):
            mod.verify_version(str(self.binary))

    def test_restore_verified_private_no_clobber(self):
        target = self.private / "restored.db"
        digest = mod.restore_verified(str(self.binary), str(self.replica), str(target),
                                      expected_sha256=self.expected, timeout=5)
        self.assertEqual(digest, self.expected)
        self.assertEqual(target.read_bytes(), self.source.read_bytes())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        with self.assertRaises(mod.SafetyError):
            mod.restore_verified(str(self.binary), str(self.replica), str(target),
                                 expected_sha256=self.expected, timeout=5)

    def test_restore_rejects_stale_target_sidecars(self):
        target = self.private / "restored.db"
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(target) + suffix)
                sidecar.write_bytes(b"stale SQLite sidecar")
                try:
                    with self.assertRaises(mod.SafetyError):
                        mod.restore_plan(str(self.replica), str(target))
                    with self.assertRaises(mod.SafetyError):
                        mod.restore_verified(str(self.binary), str(self.replica),
                                             str(target), expected_sha256=self.expected,
                                             timeout=5)
                    self.assertFalse(target.exists())
                finally:
                    sidecar.unlink()
        dangling = Path(str(target) + "-wal")
        dangling.symlink_to(self.private / "missing-sidecar")
        try:
            with self.assertRaises(mod.SafetyError):
                mod.restore_plan(str(self.replica), str(target))
        finally:
            dangling.unlink()

    def test_restore_requires_independent_anchor(self):
        target = self.private / "restored.db"
        with self.assertRaises(mod.SafetyError):
            mod.restore_verified(str(self.binary), str(self.replica), str(target),
                                 expected_sha256="", timeout=5)
        self.assertFalse(target.exists())

    def test_restore_rejects_tampered_digest_without_publish(self):
        target = self.private / "restored.db"
        with self.assertRaises(mod.SafetyError):
            mod.restore_verified(str(self.binary), str(self.replica), str(target),
                                 expected_sha256="a" * 64, timeout=5)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.private.glob(".mojum-litestream-*")))

    def test_restore_backend_failure_and_missing_output(self):
        target = self.private / "restored.db"
        for code in ("failed", "missing", "corrupt", "sidecar"):
            self._write_fake(code=code)
            with self.subTest(code=code), self.assertRaises(mod.SafetyError):
                mod.restore_verified(str(self.binary), str(self.replica), str(target),
                                     expected_sha256=self.expected, timeout=5)
            self.assertFalse(target.exists())

    def test_restore_rejects_world_readable_output_parent(self):
        public = Path(self.tmp.name) / "public"
        public.mkdir(mode=0o755)
        with self.assertRaises(mod.SafetyError):
            mod.restore_plan(str(self.replica), str(public / "out.db"))

    def test_restore_timeout_and_bytes_limits(self):
        target = self.private / "restored.db"
        for timeout, max_bytes in ((0, mod.MAX_DB_BYTES), (70, mod.MAX_DB_BYTES),
                                   (5, mod.MAX_DB_BYTES + 1)):
            with self.subTest(timeout=timeout, max_bytes=max_bytes), self.assertRaises(mod.SafetyError):
                mod.restore_verified(str(self.binary), str(self.replica), str(target),
                                     expected_sha256=self.expected, timeout=timeout,
                                     max_bytes=max_bytes)

    @unittest.skipUnless(os.name == "posix", "POSIX process group required")
    def test_replicate_timebox_stops_fake_daemon(self):
        mod.replicate_for(str(self.binary), str(self.source), str(self.replica), seconds=1)

    def test_replicate_abnormal_exit_refused(self):
        self._write_fake(code="failed")
        with self.assertRaises(mod.SafetyError):
            mod.replicate_for(str(self.binary), str(self.source), str(self.replica), seconds=1)


if __name__ == "__main__":
    unittest.main()
