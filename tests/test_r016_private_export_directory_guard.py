"""R0.16 native PRIVATE trace exports require an owner-only output directory.

Real disposable versioned segmented SQLite; no user PRIVATE data, no
chmod to operator paths, no production deployment or hardware power test.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium
from mojum.aquarium_trace_export import export_private_trace

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text("utf-8"))


@unittest.skipUnless(os.name == "posix", "POSIX ownership and mode contract")
class PrivateExportOutputDirectoryQA(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mojum-private-output-")
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.db = self.home / "synthetic.segmented.private.sqlite3"
        self.output = self.home / "owner-private"
        self.output.mkdir(mode=0o700)
        os.chmod(self.output, 0o700)
        opts = dict(
            exploration_mode="goal_free", neural_mode="disabled",
            developmental_policy="local_cell_v4", sensory_mode="none",
            journal_backend="segmented", journal_segment_size=64,
        )
        with Aquarium(self.db, SEED, GRAPH, **opts) as a:
            a.step()
            a.step()
            self.head = a.checkpoint()["journal_head"]
            self.assertEqual(a.store.count(), 3)
        self.before = self.db.read_bytes()
        self.opts = {
            **opts, "allow_private_export": True,
            "expected_journal_head": self.head,
        }

    def export(self, target):
        return export_private_trace(
            self.db, target, SEED, GRAPH, **self.opts,
        )

    def assert_rejected_unchanged(self, target, keyword):
        with self.assertRaisesRegex(PermissionError, keyword):
            self.export(target)
        self.assertFalse(target.exists())
        self.assertEqual(self.db.read_bytes(), self.before)
        self.assertFalse(list(self.home.rglob(".mojum-private-trace-*.jsonl")))

    def test_owner_0700_allows_native_export_and_private_0600_file(self):
        target = self.output / "verified.jsonl"
        result = self.export(target)
        self.assertEqual(result["frames"], 2)
        self.assertEqual(result["journal_head"], self.head)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        rows = [json.loads(x) for x in target.read_text("utf-8").splitlines()]
        self.assertEqual([x["tick"] for x in rows[:-1]], [0, 1])
        self.assertEqual(rows[-1]["journal_head"], self.head)
        self.assertEqual(self.db.read_bytes(), self.before)

    def test_shared_or_nontraversable_parents_reject_before_any_private_stage(self):
        target = self.output / "must-not-exist.jsonl"
        for mode in (0o755, 0o750, 0o705, 0o770, 0o777, 0o600, 0o300):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.output, mode)
                try:
                    with self.assertRaisesRegex(PermissionError, "directory"):
                        self.export(target)
                    self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), mode)
                finally:
                    # A 0600 directory cannot be traversed by an unprivileged
                    # caller. Inspect file existence only AFTER restoring 0700:
                    # failing to stat an inaccessible path is not publication.
                    os.chmod(self.output, 0o700)
                self.assertFalse(target.exists())
                self.assertEqual(self.db.read_bytes(), self.before)
                self.assertFalse(
                    list(self.home.rglob(".mojum-private-trace-*.jsonl"))
                )

    def test_wrong_owner_is_rejected_without_chown_or_source_mutation(self):
        target = self.output / "must-not-exist.jsonl"
        # Never actually chown user files. Simulate the owner mismatch
        # only for the exporter's initial directory preflight call.
        with patch("mojum.aquarium_trace_export.os.geteuid",
                   return_value=os.geteuid() + 1):
            self.assert_rejected_unchanged(target, "owner")
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)

    def test_symlinked_ancestor_rejected_even_if_final_parent_is_real(self):
        real = self.home / "real"
        nested = real / "nested"
        nested.mkdir(parents=True, mode=0o700)
        os.chmod(nested, 0o700)
        link = self.home / "alias"
        link.symlink_to(real, target_is_directory=True)
        target = link / "nested" / "no-export.jsonl"
        self.assertFalse(target.parent.is_symlink())
        self.assert_rejected_unchanged(target, "symlink")
        self.assertFalse((nested / "no-export.jsonl").exists())

    def test_direct_symlinked_parent_remains_refused(self):
        link = self.home / "alias-direct"
        link.symlink_to(self.output, target_is_directory=True)
        target = link / "no-export.jsonl"
        with self.assertRaisesRegex(ValueError, "trusted directory"):
            self.export(target)
        self.assertFalse((self.output / target.name).exists())
        self.assertEqual(self.db.read_bytes(), self.before)

    def test_no_clobber_even_in_trusted_owner_only_folder(self):
        target = self.output / "occupied.jsonl"
        target.write_bytes(b"EXISTING PRIVATE BYTES")
        with self.assertRaisesRegex(ValueError, "new filename"):
            self.export(target)
        self.assertEqual(target.read_bytes(), b"EXISTING PRIVATE BYTES")
        self.assertEqual(self.db.read_bytes(), self.before)


if __name__ == "__main__":
    unittest.main()
