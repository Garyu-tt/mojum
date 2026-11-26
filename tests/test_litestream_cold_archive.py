"""Offline Litestream-compatible subprocess contract and real Aquarium WAL tests.

A tiny fake executable exercises the command and recovery boundary; the real
Litestream 0.5.18 binary is NOT installed by this test and needs separate QA.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.aquarium_cold_copy import _sha256
from mojum.litestream_cold_archive import (
    LitestreamArchiveError, SCHEMA, create_local_archive,
    restore_local_archive,
)

import json

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


@unittest.skipUnless(os.name == "posix", "secure POSIX-only local fixture")
class LitestreamColdArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "source.sqlite3"
        self.replica = self.root / "replica"
        self.dest = self.root / "restored.sqlite3"
        self.binary = self.root / "fake-litestream"
        self._write_binary()
        self.pin = _sha256(self.binary)

    def _write_binary(self, *, broken=False, version="0.5.18"):
        script = f"#!{sys.executable}\n" + """
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit, unquote

args = sys.argv[1:]
if args == ["version"]:
    print("litestream vVERSION")
elif args[0] == "replicate":
    if args[1:3] != ["-once", "-force-snapshot"] or len(args) != 5:
        sys.exit(13)
    replica = Path(unquote(urlsplit(args[4]).path))
    shutil.copyfile(args[3], replica / "payload.sqlite3")
    if BROKEN:
        sys.exit(14)
elif args[0] == "restore":
    if args[1:4] != ["-integrity-check", "full", "-o"] or len(args) != 6:
        sys.exit(15)
    replica = Path(unquote(urlsplit(args[5]).path))
    shutil.copyfile(replica / "payload.sqlite3", args[4])
else:
    sys.exit(16)
""".replace("VERSION", version).replace("BROKEN", "True" if broken else "False")
        self.binary.write_text(script, encoding="utf-8")
        self.binary.chmod(0o700)

    def _source(self, steps=3):
        with Aquarium(self.source, SEED, GRAPH) as aquarium:
            for _ in range(steps):
                aquarium.step(dx=0.02)
            return aquarium.checkpoint()

    def _backup(self, **overrides):
        args = dict(
            binary=self.binary, binary_sha256=self.pin,
            public_synthetic=True,
        )
        args.update(overrides)
        return create_local_archive(
            self.source, self.replica, SEED, GRAPH, **args,
        )

    def _restore(self, info, **overrides):
        args = dict(
            binary=self.binary, binary_sha256=self.pin,
            public_synthetic=True,
            expected_agent_id=info["agent_id"],
            expected_journal_head=info["journal_head"],
            expected_sha256=info["restored_sha256"],
            expected_tick=info["tick"],
        )
        args.update(overrides)
        return restore_local_archive(
            self.replica, self.dest, SEED, GRAPH, **args,
        )

    def test_live_wal_source_unmodified_roundtrip_and_resume(self):
        with Aquarium(self.source, SEED, GRAPH) as source:
            for _ in range(5):
                source.step(dx=0.01)
            checkpoint = source.checkpoint()
            raw_before = self.source.read_bytes()
            wal = self.source.with_name(self.source.name + "-wal")
            wal_before = wal.read_bytes()
            info = self._backup()
            self.assertEqual(self.source.read_bytes(), raw_before)
            self.assertEqual(wal.read_bytes(), wal_before)
            self.assertEqual(info["schema"], SCHEMA)
            self.assertEqual(info["journal_head"], checkpoint["journal_head"])
            self.assertEqual(info["event_count"], 6)
            self.assertFalse(info["encrypted"])
            self.assertFalse(info["continuous_replication"])
            result = self._restore(info)
            self.assertEqual(result["tick"], 5)
            self.assertEqual(result["restored_sha256"], _sha256(self.dest))
            self.assertEqual(self.dest.stat().st_mode & 0o777, 0o600)
            with Aquarium(self.dest, SEED, GRAPH) as restored:
                self.assertEqual(restored.checkpoint(), checkpoint)
                restored.step()
                self.assertEqual(restored.tick, 6)
            self.assertEqual(source.checkpoint(), checkpoint)

    def test_unencrypted_mode_needs_explicit_public_synthetic_optin(self):
        self._source()
        with self.assertRaisesRegex(LitestreamArchiveError, "public_synthetic"):
            self._backup(public_synthetic=False)
        self.assertFalse(self.replica.exists())

    def test_replica_path_cannot_preexist(self):
        self._source()
        self.replica.mkdir()
        with self.assertRaises(FileExistsError):
            self._backup()

    def test_restore_cannot_overwrite_destination_or_sidecar(self):
        self._source()
        info = self._backup()
        self.dest.write_text("DO NOT REPLACE", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self._restore(info)
        self.assertEqual(self.dest.read_text(encoding="utf-8"), "DO NOT REPLACE")
        self.dest.unlink()
        Path(str(self.dest) + "-wal").write_bytes(b"sentinel")
        with self.assertRaises(FileExistsError):
            self._restore(info)

    def test_corrupted_replica_rejected_no_file_published(self):
        self._source()
        info = self._backup()
        payload = self.replica / "payload.sqlite3"
        with payload.open("r+b") as f:
            f.seek(42)
            f.write(b"altered")
        with self.assertRaises(LitestreamArchiveError):
            self._restore(info)
        self.assertFalse(self.dest.exists())

    def test_independent_hash_and_head_are_mandatory(self):
        self._source()
        info = self._backup()
        for kw in (
            {"expected_sha256": None},
            {"expected_journal_head": "not-a-head"},
            {"expected_sha256": "0" * 64},
        ):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self._restore(info, **kw)
            self.assertFalse(self.dest.exists())

    def test_empty_directory_fanout_and_depth_are_bounded(self):
        self._source()
        info = self._backup()
        # A replica containing one valid payload and hundreds of empty
        # directories must not bypass the file/byte budget during restore.
        empty = [self.replica / f"empty-{i:03d}" for i in range(129)]
        for folder in empty:
            folder.mkdir(mode=0o700)
        with self.assertRaisesRegex(LitestreamArchiveError, "directory budget"):
            self._restore(info)
        self.assertFalse(self.dest.exists())
        for folder in empty:
            folder.rmdir()
        nested = self.replica
        for i in range(17):
            nested = nested / f"depth-{i:02d}"
            nested.mkdir(mode=0o700)
        with self.assertRaisesRegex(LitestreamArchiveError, "directory budget"):
            self._restore(info)
        self.assertFalse(self.dest.exists())

    def test_symlink_replica_entry_is_never_followed(self):
        self._source()
        info = self._backup()
        (self.replica / "linked").symlink_to(self.source)
        with self.assertRaisesRegex(LitestreamArchiveError, "unsafe replica entry"):
            self._restore(info)
        self.assertFalse(self.dest.exists())

    def test_source_missing_never_issues_a_successful_archive(self):
        with self.assertRaises(ValueError):
            self._backup()
        self.assertFalse(self.replica.exists())

    def test_broken_native_command_cleans_partial_replica(self):
        self._source()
        self._write_binary(broken=True)
        self.pin = _sha256(self.binary)
        with self.assertRaisesRegex(LitestreamArchiveError, "output redacted"):
            self._backup()
        self.assertFalse(self.replica.exists())

    def test_executable_checksum_version_and_permissions_pinned(self):
        self._source()
        with self.assertRaisesRegex(LitestreamArchiveError, "SHA-256 mismatch"):
            self._backup(binary_sha256="0" * 64)
        self._write_binary(version="0.5.17")
        self.pin = _sha256(self.binary)
        with self.assertRaisesRegex(LitestreamArchiveError, "exactly 0.5.18"):
            self._backup()
        self._write_binary()
        self.pin = _sha256(self.binary)
        self.binary.chmod(0o722)
        with self.assertRaisesRegex(LitestreamArchiveError, "not group/world writable"):
            self._backup()
        self.assertFalse(self.replica.exists())

    def test_unsafe_replica_parent_rejected(self):
        self._source()
        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o755)
        unsafe.chmod(0o755)
        with self.assertRaisesRegex(LitestreamArchiveError, "owner-only"):
            create_local_archive(
                self.source, unsafe / "replica", SEED, GRAPH,
                binary=self.binary, binary_sha256=self.pin,
                public_synthetic=True,
            )

    def test_unsafe_archive_permissions_rejected(self):
        self._source()
        info = self._backup()
        (self.replica / "payload.sqlite3").chmod(0o644)
        with self.assertRaisesRegex(LitestreamArchiveError, "unsafe replica file"):
            self._restore(info)
        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main()
