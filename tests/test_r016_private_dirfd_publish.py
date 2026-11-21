"""Native PRIVATE JSONL publication must remain anchored to a trusted dirfd.

All tests are unprivileged POSIX and use disposable synthetic segmented-v4
SQLite. An injected attacker controls the *path alias* and can plant a
target or temp-name collision, but may never read user data or modify a
production filesystem. Files which became visible before a detected race
are durability-UNCERTAIN, not automatically deleted or treated as a
successful export.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium
from mojum.aquarium_trace_export import (
    export_private_trace, _open_owner_only_directory_fd,
)

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


@unittest.skipUnless(os.name == "posix", "dirfd/openat POSIX semantics")
class AnchoredPrivateTracePublicationQA(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mojum-r016-dirfd-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "synthetic.private.sqlite3"
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        os.chmod(self.private, 0o700)
        opts = dict(
            neural_mode="disabled", exploration_mode="goal_free",
            sensory_mode="none", developmental_policy="local_cell_v4",
            journal_backend="segmented", journal_segment_size=64,
        )
        with Aquarium(self.db, SEED, GRAPH, **opts) as a:
            for _ in range(4):
                a.step()
            self.head = a.checkpoint()["journal_head"]
            self.assertEqual(a.store.count(), 5)
        self.original = self.db.read_bytes()
        self.opts = {
            **opts, "allow_private_export": True,
            "expected_journal_head": self.head,
        }

    def export(self, dest):
        return export_private_trace(
            self.db, dest, SEED, GRAPH, **self.opts
        )

    def assert_source_unchanged(self):
        self.assertEqual(self.db.read_bytes(), self.original)

    def test_parent_alias_swap_during_link_cannot_redirect_private_data(self):
        # A hostile rename/alias race occurs AFTER the native publisher has
        # checked the parent path and anchored a real private directory fd.
        attacker = self.root / "alternate-private"
        attacker.mkdir(mode=0o700)
        os.chmod(attacker, 0o700)
        relocated = self.root / "old-private-inode"
        target = self.private / "published.jsonl"
        original_link = os.link
        seen = []

        def path_swap_just_before_link(src, dst, **kwargs):
            self.assertTrue(kwargs["src_dir_fd"] == kwargs["dst_dir_fd"])
            self.assertIsInstance(kwargs["src_dir_fd"], int)
            os.rename(self.private, relocated)
            self.private.symlink_to(attacker, target_is_directory=True)
            seen.append("swapped")
            return original_link(src, dst, **kwargs)

        with patch("mojum.aquarium_trace_export.os.link",
                   side_effect=path_swap_just_before_link):
            with self.assertRaises((OSError, PermissionError)):
                self.export(target)
        self.assertEqual(seen, ["swapped"])
        self.assertFalse((attacker / target.name).exists(),
                         "swapped pathname must not receive PRIVATE JSONL")
        anchored_output = relocated / target.name
        self.assertTrue(anchored_output.is_file())
        self.assertEqual(stat.S_IMODE(anchored_output.stat().st_mode), 0o600)
        self.assertEqual(list(relocated.glob(".mojum-private-trace-*.jsonl")), [])
        self.assert_source_unchanged()
        # Final file is *visible* but export returned failure (no false
        # durability acknowledgement). Manual reconcile only.

    def test_symlink_inserted_between_preflight_and_anchored_open_is_refused(self):
        target = self.private / "never.jsonl"
        relocated = self.root / "moved-private"
        attacker = self.root / "foreign"
        attacker.mkdir(mode=0o700)
        os.chmod(attacker, 0o700)
        actual_open = _open_owner_only_directory_fd
        triggered = []

        def replace_just_before_anchor(directory):
            if not triggered:
                os.rename(self.private, relocated)
                self.private.symlink_to(attacker, target_is_directory=True)
                triggered.append(True)
            return actual_open(directory)

        with patch("mojum.aquarium_trace_export._open_owner_only_directory_fd",
                   side_effect=replace_just_before_anchor):
            with self.assertRaises(OSError):
                self.export(target)
        self.assertEqual(len(triggered), 1)
        self.assertFalse((attacker / target.name).exists())
        self.assertFalse((relocated / target.name).exists())
        self.assertFalse(list(relocated.glob(".mojum-private-trace-*.jsonl")))
        self.assert_source_unchanged()

    def test_competing_output_created_at_link_is_never_overwritten(self):
        dest = self.private / "race.jsonl"
        original_link = os.link
        competing = b"COMPETING FILE MUST BE PRESERVED"
        actions = []

        def competing_create_before_link(src, dst, **kwargs):
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                         0o600, dir_fd=kwargs["dst_dir_fd"])
            try:
                os.write(fd, competing)
            finally:
                os.close(fd)
            actions.append("created_other")
            return original_link(src, dst, **kwargs)

        with patch("mojum.aquarium_trace_export.os.link",
                   side_effect=competing_create_before_link):
            with self.assertRaises(FileExistsError):
                self.export(dest)
        self.assertEqual(actions, ["created_other"])
        self.assertEqual(dest.read_bytes(), competing)
        self.assertEqual(list(self.private.glob(".mojum-private-trace-*.jsonl")), [])
        self.assert_source_unchanged()

    def test_adversarial_random_stage_name_collisions_refuse_without_clobber(self):
        candidate = ".mojum-private-trace-collisiontoken12345.jsonl"
        victim = self.private / candidate
        victim.write_bytes(b"EXISTING SECRET CANNOT BE REPLACED")
        os.chmod(victim, 0o600)
        target = self.private / "no-export.jsonl"
        with patch("mojum.aquarium_trace_export.secrets.token_urlsafe",
                   return_value="collisiontoken12345"):
            with self.assertRaises(FileExistsError):
                self.export(target)
        self.assertFalse(target.exists())
        self.assertEqual(victim.read_bytes(), b"EXISTING SECRET CANNOT BE REPLACED")
        self.assertEqual(
            list(self.private.glob(".mojum-private-trace-*.jsonl")), [victim],
        )
        self.assert_source_unchanged()

    def test_directory_descriptors_close_after_success_and_after_no_clobber_failure(self):
        from mojum import aquarium_trace_export as exporter
        original_open = exporter._open_owner_only_directory_fd
        opened = []

        def tracked_open(dirname):
            fd = original_open(dirname)
            opened.append(fd)
            return fd

        target = self.private / "complete.jsonl"
        with patch("mojum.aquarium_trace_export._open_owner_only_directory_fd",
                   side_effect=tracked_open):
            result = self.export(target)
        self.assertEqual(result["frames"], 4)
        self.assertGreaterEqual(len(opened), 3)
        for fd in opened:
            with self.assertRaises(OSError) as caught:
                os.fstat(fd)
            self.assertEqual(caught.exception.errno, errno.EBADF)

        opened.clear()
        with patch("mojum.aquarium_trace_export._open_owner_only_directory_fd",
                   side_effect=tracked_open):
            with self.assertRaises(ValueError):
                self.export(target)
        # Pre-existing output is refused by preflight; no new descriptor.
        self.assertEqual(opened, [])
        self.assert_source_unchanged()


if __name__ == "__main__":
    unittest.main()
