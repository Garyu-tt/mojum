"""POSIX output-directory sync acceptance for native PRIVATE Aquarium export.

A successful local export must fsync the data file, atomically publish
its new hard-link, unlink the PRIVATE staging name and then fsync the
parent directory. This is a syscall/order contract only, never evidence
of battery/power loss survivability on a real device.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium
from mojum.aquarium_trace_export import export_private_trace

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))

BEFORE_DIRSYNC = r'''
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from mojum.aquarium_trace_export import export_private_trace
db, dest, head, root = sys.argv[1:]
root = Path(root)
seed = json.loads((root / "examples/genesis_seed.json").read_text("utf-8"))
graph = json.loads((root / "examples/first_loop.json").read_text("utf-8"))
with patch("mojum.aquarium_trace_export._fsync_published_directory",
           side_effect=lambda directory: os._exit(73)):
    export_private_trace(
        db, dest, seed, graph,
        allow_private_export=True, expected_journal_head=head,
        neural_mode="disabled", exploration_mode="goal_free",
        developmental_policy="local_cell_v4",
        journal_backend="segmented", journal_segment_size=64,
        sensory_mode="none",
    )
'''


@unittest.skipUnless(os.name == "posix", "POSIX directory sync semantics only")
class OutputDirectorySyncQA(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mojum-directory-durability-")
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.db = self.directory / "segmented.private.sqlite3"
        opts = dict(
            neural_mode="disabled", exploration_mode="goal_free",
            developmental_policy="local_cell_v4", sensory_mode="none",
            journal_backend="segmented", journal_segment_size=64,
        )
        with Aquarium(self.db, SEED, GRAPH, **opts) as source:
            self.frames = [source.step() for _ in range(3)]
            self.head = source.checkpoint()["journal_head"]
            self.assertEqual(source.store.count(), 4)
        self.original_bytes = self.db.read_bytes()
        self.opts = {**opts, "allow_private_export": True,
                     "expected_journal_head": self.head}

    def export(self, filename):
        return export_private_trace(
            self.db, self.directory / filename, SEED, GRAPH, **self.opts
        )

    def assert_valid_output(self, path):
        rows = path.read_bytes().splitlines(keepends=True)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(x.endswith(b"\n") for x in rows))
        frames = [json.loads(s) for s in rows[:-1]]
        footer = json.loads(rows[-1])
        self.assertEqual(frames, self.frames)
        self.assertEqual(footer["schema"], "mojum.aquarium.checkpoint/2")
        self.assertEqual(footer["policy_schema"], "mojum.developmental.local-cell/4")
        self.assertEqual(footer["journal_head"], self.head)
        self.assertEqual(footer["trace_integrity"]["sha256"],
                         hashlib.sha256(b"".join(rows[:-1])).hexdigest())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_success_fsyncs_file_then_directory_after_private_stage_unlink(self):
        seen = []
        original = os.fsync
        original_fchmod = os.fchmod

        def audited_fchmod(fd, mode):
            self.assertEqual(mode, 0o600)
            self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
            seen.append("mode")
            return original_fchmod(fd, mode)

        def audited_fsync(fd):
            info = os.fstat(fd)
            if stat.S_ISDIR(info.st_mode):
                self.assertTrue((self.directory / "complete.jsonl").is_file())
                self.assertEqual(
                    list(self.directory.glob(".mojum-private-trace-*.jsonl")),
                    [], "directory must be flushed after stage unlink"
                )
                seen.append("directory")
            elif stat.S_ISREG(info.st_mode):
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
                self.assertEqual(seen, ["mode"],
                                 "mode must be finalized before file fsync")
                seen.append("file")
            else:
                self.fail("fsync encountered unexpected descriptor type")
            return original(fd)

        with patch("mojum.aquarium_trace_export.os.fchmod",
                   side_effect=audited_fchmod), \
             patch("mojum.aquarium_trace_export.os.fsync",
                   side_effect=audited_fsync):
            result = self.export("complete.jsonl")
        self.assertEqual(seen, ["mode", "file", "directory"])
        self.assertEqual(result["frames"], 3)
        self.assert_valid_output(self.directory / "complete.jsonl")
        self.assertEqual(self.db.read_bytes(), self.original_bytes)

    def test_failure_to_sync_parent_never_claims_success_and_retains_complete_private_output(self):
        events = []
        original = os.fsync

        def fail_dir_only(fd):
            info = os.fstat(fd)
            if stat.S_ISDIR(info.st_mode):
                events.append("directory_failed")
                raise OSError(errno.EIO, "synthetic directory fsync failure")
            events.append("file_synced")
            return original(fd)

        path = self.directory / "durability-uncertain.jsonl"
        with patch("mojum.aquarium_trace_export.os.fsync",
                   side_effect=fail_dir_only):
            with self.assertRaisesRegex(OSError, "directory fsync failure"):
                self.export(path.name)
        self.assertEqual(events, ["file_synced", "directory_failed"])
        # The link may already be visible, but has no acknowledged success.
        # Never delete/overwrite it automatically: operator reconciliation.
        self.assert_valid_output(path)
        self.assertEqual(
            list(self.directory.glob(".mojum-private-trace-*.jsonl")), []
        )
        with self.assertRaisesRegex(ValueError, "new filename"):
            self.export(path.name)
        self.assertEqual(self.db.read_bytes(), self.original_bytes)

    def test_private_mode_hardening_failure_does_not_publish_any_jsonl(self):
        # The tempfile is 0600 by construction; nevertheless a failed
        # explicit inode-mode syscall must NEVER report success or publish.
        dest = self.directory / "mode-failed.jsonl"
        with patch("mojum.aquarium_trace_export.os.fchmod",
                   side_effect=OSError(errno.EPERM, "synthetic fchmod denial")):
            with self.assertRaisesRegex(OSError, "fchmod denial"):
                self.export(dest.name)
        self.assertFalse(dest.exists())
        self.assertEqual(
            list(self.directory.glob(".mojum-private-trace-*.jsonl")), []
        )
        self.assertEqual(self.db.read_bytes(), self.original_bytes)

    def test_file_fsync_failure_does_not_publish_or_acknowledge(self):
        dest = self.directory / "file-sync-failed.jsonl"
        original = os.fsync
        seen = []

        def fail_file_only(fd):
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode):
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
                seen.append("file_sync_failed")
                raise OSError(errno.EIO, "synthetic data fsync failure")
            self.fail("directory fsync cannot precede successful file sync")

        with patch("mojum.aquarium_trace_export.os.fsync",
                   side_effect=fail_file_only):
            with self.assertRaisesRegex(OSError, "data fsync failure"):
                self.export(dest.name)
        self.assertEqual(seen, ["file_sync_failed"])
        self.assertFalse(dest.exists())
        self.assertEqual(
            list(self.directory.glob(".mojum-private-trace-*.jsonl")), []
        )
        self.assertEqual(self.db.read_bytes(), self.original_bytes)

    def test_no_clobber_does_not_open_or_fsync_parent_directory(self):
        output = self.directory / "occupied.jsonl"
        output.write_bytes(b"PRIVATE EXISTING FILE")
        original = output.read_bytes()
        with patch("mojum.aquarium_trace_export.os.fsync") as fsync:
            with self.assertRaisesRegex(ValueError, "new filename"):
                self.export(output.name)
            fsync.assert_not_called()
        self.assertEqual(output.read_bytes(), original)

    def test_process_exit_after_unlink_before_directory_fsync_cannot_acknowledge_durability(self):
        target = self.directory / "published-before-dir-fsync.jsonl"
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        result = subprocess.run(
            [sys.executable, "-c", BEFORE_DIRSYNC, str(self.db), str(target),
             self.head, str(ROOT)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=90,
            check=False,
        )
        self.assertEqual(result.returncode, 73, result.stderr[-1000:])
        # Process died before calling fsync on parent directory. The
        # destination is complete/visible, but actual power durability
        # CANNOT be inferred from the appearance of its directory entry.
        self.assert_valid_output(target)
        self.assertEqual(
            list(self.directory.glob(".mojum-private-trace-*.jsonl")), []
        )
        self.assertEqual(self.db.read_bytes(), self.original_bytes)


if __name__ == "__main__":
    unittest.main()
