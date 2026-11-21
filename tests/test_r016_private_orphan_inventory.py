"""Real disposable PRIVATE trace staging inventory and crash residue review.

The scanner NEVER deletes, repairs, renames, chmods or imports source
records. All fixtures are disposable synthetic SQLite/JSONL; caller may
separately pin an original event-chain head for an extra equality check.
"""
from __future__ import annotations

import copy
import hashlib
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


    def test_real_crashed_export_process_leaves_audit_only_private_stage(self):
        # Actual production code, NOT a handcrafted JSONL: terminate before
        # atomic link after the file was flushed/fsynced, and verify scanner
        # sees the surviving 0600 stage without creating/altering final.
        script = r"""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from mojum.aquarium_trace_export import export_private_trace
source, dest, head, root, mode = sys.argv[1:]
root = Path(root)
seed = json.loads((root / "examples/genesis_seed.json").read_text("utf-8"))
graph = json.loads((root / "examples/first_loop.json").read_text("utf-8"))
if mode == "before":
    with patch("mojum.aquarium_trace_export.os.link",
               side_effect=lambda src, dst: os._exit(71)):
        export_private_trace(
            source, dest, seed, graph,
            allow_private_export=True, expected_journal_head=head,
            neural_mode="disabled", exploration_mode="goal_free",
            developmental_policy="local_cell_v4", sensory_mode="none",
            journal_backend="segmented", journal_segment_size=64,
        )
else:
    real_link = os.link
    def link_then_die(src, dst):
        real_link(src, dst)
        os._exit(72)
    with patch("mojum.aquarium_trace_export.os.link",
               side_effect=link_then_die):
        export_private_trace(
            source, dest, seed, graph,
            allow_private_export=True, expected_journal_head=head,
            neural_mode="disabled", exploration_mode="goal_free",
            developmental_policy="local_cell_v4", sensory_mode="none",
            journal_backend="segmented", journal_segment_size=64,
        )
"""
        for mode, code in (("before", 71), ("after", 72)):
            with self.subTest(crash=mode):
                final = self.directory / ("crashed-" + mode + ".jsonl")
                env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
                result = subprocess.run(
                    [sys.executable, "-c", script, str(self.source),
                     str(final), self.head, str(ROOT), mode],
                    cwd=ROOT, env=env, capture_output=True, text=True,
                    timeout=60,
                )
                self.assertEqual(result.returncode, code, result.stderr[-700:])
                audit = inspect_private_trace_orphans(
                    self.directory, expected_journal_head=self.head
                )
                statuses = {r["name"]: r for r in audit["files"]}
                self.assertEqual(len(statuses), 1)
                record = next(iter(statuses.values()))
                if mode == "before":
                    self.assertFalse(final.exists())
                    self.assertEqual(record["status"],
                                     "self_consistent_head_matches")
                    self.assertEqual(record["hard_links"], 1)
                else:
                    self.assertTrue(final.is_file())
                    self.assertEqual(record["status"],
                                     "linked_manual_reconciliation")
                    self.assertEqual(record["hard_links"], 2)
                    self.assertEqual(final.read_bytes(), self.canonical_bytes)
                self.assertEqual(self.source.read_bytes(), self.original_bytes)
                # Only inside this disposable synthetic QA directory.
                for stage in self.directory.glob(
                    ".mojum-private-trace-*.jsonl"
                ):
                    stage.unlink()


    def test_v4_adversarial_fields_refused_even_with_recomputed_frame_hash_and_matching_head(self):
        # All attacking files derive from REAL Aquarium frames and preserve
        # the original independent source head. Recomputed SHA and canonical
        # encoding should never turn incoherent temporal history into data
        # marked as self_consistent_head_matches by the incident auditor.
        rows = [json.loads(x) for x in self.canonical_bytes.splitlines()]
        real_frames, original_footer = rows[:-1], rows[-1]
        self.assertEqual(len(real_frames), 3)
        self.assertEqual(original_footer["journal_head"], self.head)
        cases = (
            ("badlearnz", ("learning", "schema"),
             "mojum.developmental.local-cell.outcome/3"),
            ("badepochz", ("observation", "epoch_tick"), 20),
            ("badintent", ("intent", "tick"), 99),
            ("badcountz", ("learning", "total_updates"), 200),
            ("badpulsez", ("observation", "heartbeat_pulse"), 92312),
            ("boolclock", ("observation", "absolute_tick"), True),
            ("badoutcz", ("learning", "schema"), "future.unknown/99"),
        )
        expected = {}

        def stage_forged(name, frames):
            wire = b"".join(
                (json.dumps(frame, sort_keys=True, ensure_ascii=False,
                            allow_nan=False, separators=(",", ":")) + "\n")
                .encode("utf-8")
                for frame in frames
            )
            footer = copy.deepcopy(original_footer)
            footer["trace_integrity"]["sha256"] = hashlib.sha256(wire).hexdigest()
            footer["trace_integrity"]["byte_length"] = len(wire)
            footer_wire = (json.dumps(
                footer, sort_keys=True, ensure_ascii=False,
                allow_nan=False, separators=(",", ":")
            ) + "\n").encode("utf-8")
            stage = self.stage(name, wire + footer_wire)
            expected[stage.name] = "incomplete_or_inconsistent"

        for name, (group, field), forged_value in cases:
            frames = copy.deepcopy(real_frames)
            frames[0]["developmental"][group][field] = forged_value
            stage_forged(name, frames)

        # Regression for None==None: absence on BOTH sides must not
        # masquerade as a matching heartbeat measurement.
        no_pulse = copy.deepcopy(real_frames)
        no_pulse[0]["developmental"]["observation"].pop("heartbeat_pulse")
        no_pulse[0]["interoception"]["heartbeat"].pop("pulse")
        stage_forged("bothgone", no_pulse)
        # The unmodified native-export file under the SAME anchored head
        # remains accepted, distinguishing format refusal from a bad digest.
        good = self.stage("untouched")
        expected[good.name] = "self_consistent_head_matches"
        audited = inspect_private_trace_orphans(
            self.directory, expected_journal_head=self.head
        )
        statuses = {row["name"]: row["status"] for row in audited["files"]}
        self.assertEqual(statuses, expected)
        self.assertEqual(self.source.read_bytes(), self.original_bytes)
        self.assertEqual(self.normal.read_bytes(), self.canonical_bytes)


if __name__ == "__main__":
    unittest.main()
