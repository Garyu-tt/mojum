"""Real disposable PRIVATE trace staging inventory and crash residue review.

The scanner NEVER deletes, repairs, renames, chmods or imports source
records. All fixtures are disposable synthetic SQLite/JSONL; caller may
separately pin an original event-chain head for an extra equality check.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.aquarium_trace_export import export_private_trace
from mojum.private_trace_orphans import (
    PrivateOrphanAuditError, inspect_private_trace_orphans,
)

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


@unittest.skipUnless(os.name == "posix", "POSIX owner-only directory inspection")
class PrivateOrphanInventoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mojum-private-orphan-audit-")
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        self.directory.chmod(0o700)
        self.source = self.directory / "synthetic-source.sqlite3"
        with Aquarium(
            self.source, SEED, GRAPH,
            neural_mode="disabled", exploration_mode="goal_free",
            developmental_policy="local_cell_v4", sensory_mode="none",
            journal_backend="segmented", journal_segment_size=64,
        ) as a:
            for _ in range(3):
                a.step()
            self.head = a.checkpoint()["journal_head"]
        self.original_bytes = self.source.read_bytes()
        self.normal = self.directory / "exported.private.jsonl"
        export_private_trace(
            self.source, self.normal, SEED, GRAPH,
            allow_private_export=True, expected_journal_head=self.head,
            neural_mode="disabled", exploration_mode="goal_free",
            developmental_policy="local_cell_v4", sensory_mode="none",
            journal_backend="segmented", journal_segment_size=64,
        )
        self.canonical_bytes = self.normal.read_bytes()
        self.assertGreater(len(self.canonical_bytes), 200)

    def stage(self, ident="abcdefgh", content=None):
        target = self.directory / (".mojum-private-trace-" + ident + ".jsonl")
        target.write_bytes(self.canonical_bytes if content is None else content)
        target.chmod(0o600)
        return target

    def test_complete_staging_requires_separately_supplied_head_for_matched_status(self):
        stage = self.stage()
        without = inspect_private_trace_orphans(self.directory)
        self.assertEqual(without["orphan_candidates"], 1)
        self.assertEqual(without["files"][0]["name"], stage.name)
        self.assertEqual(without["files"][0]["status"], "self_consistent_unanchored")
        with_head = inspect_private_trace_orphans(
            self.directory, expected_journal_head=self.head
        )
        self.assertEqual(
            with_head["files"][0]["status"], "self_consistent_head_matches"
        )
        self.assertEqual(with_head["files"][0]["hard_links"], 1)
        self.assertEqual(with_head["files"][0]["bytes"], len(self.canonical_bytes))
        self.assertNotIn("journal_head", str(with_head))
        self.assertNotIn(self.head, str(with_head))
        self.assertNotIn("developmental_state", str(with_head))
        self.assertEqual(stage.read_bytes(), self.canonical_bytes)
        self.assertEqual(self.source.read_bytes(), self.original_bytes)

    def test_bad_head_and_incomplete_stage_are_not_accepted(self):
        good = self.stage("headhead")
        foreign = inspect_private_trace_orphans(
            self.directory, expected_journal_head="f" * 64
        )["files"]
        self.assertEqual(foreign[0]["status"], "independent_head_mismatch")
        self.assertEqual(good.read_bytes(), self.canonical_bytes)
        bad = self.stage("truncated", self.canonical_bytes[:-37])
        result = inspect_private_trace_orphans(
            self.directory, expected_journal_head=self.head
        )
        statuses = {x["name"]: x["status"] for x in result["files"]}
        self.assertEqual(statuses[good.name], "self_consistent_head_matches")
        self.assertEqual(statuses[bad.name], "incomplete_or_inconsistent")
        self.assertEqual(bad.read_bytes(), self.canonical_bytes[:-37])

    def test_duplicate_json_member_or_frame_tamper_fails_even_with_valid_old_footer(self):
        first, rest = self.canonical_bytes.split(b"\n", 1)
        # Strict duplicate-key rejection: unlike json.loads default, two
        # "tick" members cannot be normalized into an apparent valid frame.
        duplicated = first.replace(b'"tick":0', b'"tick":0,"tick":0') + b"\n" + rest
        self.stage("duplicate", duplicated)
        modified = self.canonical_bytes.replace(b'"tick":0', b'"tick":2', 1)
        self.stage("tampered", modified)
        statuses = {x["name"]: x["status"] for x in
                    inspect_private_trace_orphans(self.directory)["files"]}
        self.assertTrue(statuses)
        self.assertEqual(set(statuses.values()), {"incomplete_or_inconsistent"})

    def test_hardlink_stage_is_not_mistaken_for_unpublished_or_safely_deletable(self):
        stage = self.directory / ".mojum-private-trace-linkedqq.jsonl"
        os.link(self.normal, stage)
        result = inspect_private_trace_orphans(
            self.directory, expected_journal_head=self.head
        )
        self.assertEqual(result["orphan_candidates"], 1)
        self.assertEqual(result["files"][0]["status"], "linked_manual_reconciliation")
        self.assertEqual(result["files"][0]["hard_links"], 2)
        self.assertEqual(stage.read_bytes(), self.normal.read_bytes())
        self.assertEqual(self.normal.read_bytes(), self.canonical_bytes)

    def test_symlink_world_readable_and_fake_directory_are_unsafe_never_opened(self):
        good = self.stage("privateqq")
        public = self.stage("publicqq")
        public.chmod(0o644)
        symlink = self.directory / ".mojum-private-trace-linklink.jsonl"
        symlink.symlink_to(self.normal)
        # Names with similar prefix but invalid suffix are not accepted as
        # evidence of being our exporter, nor scanned recursively.
        other = self.directory / ".mojum-private-trace-invalid.jsonl.exe"
        other.write_bytes(b"not a private export")
        nested = self.directory / "nested"
        nested.mkdir()
        (nested / ".mojum-private-trace-childchild.jsonl").write_text(
            "not a scan target", encoding="utf-8"
        )
        statuses = {x["name"]: x["status"] for x in
                    inspect_private_trace_orphans(self.directory)["files"]}
        self.assertEqual(len(statuses), 3)
        self.assertEqual(statuses[good.name], "self_consistent_unanchored")
        self.assertEqual(statuses[public.name], "unsafe_entry")
        self.assertEqual(statuses[symlink.name], "unsafe_entry")
        self.assertEqual(self.normal.read_bytes(), self.canonical_bytes)

    def test_refuse_world_readable_or_symlink_scanner_root_and_bad_expected_head(self):
        self.stage()
        self.directory.chmod(0o755)
        with self.assertRaises(PrivateOrphanAuditError):
            inspect_private_trace_orphans(self.directory)
        self.directory.chmod(0o700)
        alias = self.directory.parent / (self.directory.name + "-alias")
        alias.symlink_to(self.directory, target_is_directory=True)
        self.addCleanup(lambda: alias.unlink(missing_ok=True))
        with self.assertRaises(PrivateOrphanAuditError):
            inspect_private_trace_orphans(alias)
        with self.assertRaises(PrivateOrphanAuditError):
            inspect_private_trace_orphans(
                self.directory, expected_journal_head="NOT A SHA"
            )

    def test_cli_outputs_metadata_only_and_nonzero_status_for_unsafe_stage(self):
        self.stage("completeq")
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        command = [
            sys.executable, "-m", "mojum.private_trace_orphans",
            "--directory", str(self.directory),
            "--expected-journal-head", self.head,
        ]
        ok = subprocess.run(command, cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=20)
        self.assertEqual(ok.returncode, 0, ok.stderr[-800:])
        result = json.loads(ok.stdout)
        self.assertEqual(result["orphan_candidates"], 1)
        self.assertEqual(
            result["files"][0]["status"], "self_consistent_head_matches"
        )
        self.assertNotIn(self.head, ok.stdout)
        self.assertNotIn("reflectance_rgb", ok.stdout)
        self.assertNotIn("developmental", ok.stdout)
        self.stage("brokenqq", b'{"partial":true}\n')
        unsafe = subprocess.run(command, cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(unsafe.returncode, 2)
        result = json.loads(unsafe.stdout)
        self.assertEqual(result["orphan_candidates"], 2)
        self.assertIn("incomplete_or_inconsistent",
                      {x["status"] for x in result["files"]})
        self.assertEqual(self.source.read_bytes(), self.original_bytes)


if __name__ == "__main__":
    unittest.main()
