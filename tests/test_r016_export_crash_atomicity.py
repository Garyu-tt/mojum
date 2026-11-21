"""R0.16 export crash/recovery and publication race acceptance on real SQLite.

Run actual exporter subprocesses against disposable opt-in segmented v4
journals. Deliberately os._exit during publish. The source is ALWAYS a
synthetic one-agent database. No network, hardware, user history, hidden
subjective-valence claims or production export. A frame SHA is NOT a
signature. Existing source is never overwritten or migrated.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium
from mojum.aquarium_trace_export import export_private_trace

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text("utf-8"))

# The crash mode and source head are command-line data, not shell commands.
# This subprocess really calls the production exporter and exits without
# finally/destructors. The kill points straddle the *atomic* hard-link.
CRASH_SCRIPT = r'''
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from mojum.aquarium_trace_export import export_private_trace
mode, source, dest, head, root = sys.argv[1:]
root = Path(root)
seed = json.loads((root / "examples/genesis_seed.json").read_text("utf-8"))
graph = json.loads((root / "examples/first_loop.json").read_text("utf-8"))
opts = dict(allow_private_export=True, expected_journal_head=head,
            exploration_mode="goal_free", developmental_policy="local_cell_v4",
            journal_backend="segmented", journal_segment_size=64,
            sensory_mode="none", neural_mode="disabled")
if mode == "before_fsync":
    with patch("mojum.aquarium_trace_export.os.fsync",
               side_effect=lambda fd: os._exit(70)):
        export_private_trace(source, dest, seed, graph, **opts)
elif mode == "before_link":
    with patch("mojum.aquarium_trace_export.os.link",
               side_effect=lambda *args: os._exit(71)):
        export_private_trace(source, dest, seed, graph, **opts)
elif mode == "after_link":
    original = os.link
    def link_and_crash(src, dst):
        original(src, dst)
        os._exit(72)
    with patch("mojum.aquarium_trace_export.os.link",
               side_effect=link_and_crash):
        export_private_trace(source, dest, seed, graph, **opts)
elif mode == "normal":
    result = export_private_trace(source, dest, seed, graph, **opts)
    assert result["event_count"] == 6
    print("OK_EXPORT_ONLY_METADATA")
else:
    raise RuntimeError("invalid controlled crash fixture")
'''

def argv_for(source, output, head, mode):
    return [
        sys.executable, "-c", CRASH_SCRIPT,
        mode, str(source), str(output), head, str(ROOT),
    ]


class ExportCrashAndPublicationQA(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mojum-r016-crash-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.db = self.home / "synthetic.private.segmented.sqlite3"
        with Aquarium(
            self.db, SEED, GRAPH, neural_mode="disabled",
            exploration_mode="goal_free", developmental_policy="local_cell_v4",
            sensory_mode="none", journal_backend="segmented",
            journal_segment_size=64,
        ) as a:
            for _ in range(5):
                a.step()
            self.head = a.checkpoint()["journal_head"]
            self.assertEqual(a.store.count(), 6)
        self.original = self.db.read_bytes()
        self.assertTrue(self.original)

    def environment(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src")
        return env

    def call(self, mode, target):
        return subprocess.run(
            argv_for(self.db, target, self.head, mode),
            cwd=ROOT, env=self.environment(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=90, check=False,
        )

    def assert_original_unchanged(self):
        self.assertEqual(self.db.read_bytes(), self.original)
        with Aquarium(
            self.db, SEED, GRAPH, neural_mode="disabled",
            exploration_mode="goal_free", developmental_policy="local_cell_v4",
            sensory_mode="none", journal_backend="segmented",
            journal_segment_size=64, external_expected_head=self.head,
        ) as reopened:
            self.assertEqual(reopened.checkpoint()["tick"], 5)
            self.assertEqual(reopened.store.count(), 6)
        self.assertEqual(self.db.read_bytes(), self.original)

    def assert_complete_jsonl(self, path):
        blob = path.read_bytes()
        entries = blob.splitlines(keepends=True)
        self.assertEqual(len(entries), 6)
        self.assertTrue(all(item.endswith(b"\n") for item in entries))
        frames = [json.loads(line) for line in entries[:-1]]
        footer = json.loads(entries[-1])
        self.assertEqual([f["tick"] for f in frames], list(range(5)))
        self.assertEqual(footer["schema"], "mojum.aquarium.checkpoint/2")
        self.assertEqual(footer["policy_schema"], "mojum.developmental.local-cell/4")
        self.assertEqual(footer["journal_head"], self.head)
        self.assertEqual(footer["stored_events"], 6)
        digest = hashlib.sha256(b"".join(entries[:-1])).hexdigest()
        self.assertEqual(footer["trace_integrity"]["sha256"], digest)
        self.assertEqual(footer["trace_integrity"]["frame_count"], 5)
        self.assertEqual(footer["epoch_boundaries"], [])
        for forbidden in ("developmental_state", "neural_state", "toy_contact_state"):
            self.assertNotIn(forbidden, footer)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX os._exit crash semantics")
    def test_forced_exit_before_fsync_or_link_never_publishes_destination(self):
        for mode, status in (("before_fsync", 70), ("before_link", 71)):
            with self.subTest(mode=mode):
                target = self.home / f"{mode}.jsonl"
                result = self.call(mode, target)
                self.assertEqual(result.returncode, status, result.stderr[-800:])
                self.assertFalse(target.exists(), "partial PRIVATE output was published")
                self.assert_original_unchanged()
                remnants = sorted(self.home.glob(".mojum-private-trace-*.jsonl"))
                self.assertTrue(remnants, "real abrupt exit should leave orphan staging")
                for remnant in remnants:
                    if os.name == "posix":
                        self.assertEqual(stat.S_IMODE(remnant.stat().st_mode), 0o600)
                    remnant.unlink()  # explicitly clean only TEST temp files

    @unittest.skipUnless(os.name == "posix", "POSIX hard-link publication semantics")
    def test_forced_exit_after_atomic_link_exposes_only_complete_private_jsonl(self):
        target = self.home / "after-link.jsonl"
        result = self.call("after_link", target)
        self.assertEqual(result.returncode, 72, result.stderr[-800:])
        self.assertTrue(target.is_file())
        self.assert_complete_jsonl(target)
        self.assert_original_unchanged()
        remnants = list(self.home.glob(".mojum-private-trace-*.jsonl"))
        self.assertEqual(len(remnants), 1,
                         "SIGKILL-style exit leaves a private temp file to quarantine")
        # Real no-clobber retry: an existing complete output is not replaced.
        original_published = target.read_bytes()
        retry = self.call("normal", target)
        self.assertNotEqual(retry.returncode, 0)
        self.assertEqual(target.read_bytes(), original_published)
        remnants[0].unlink()

    @unittest.skipUnless(os.name == "posix", "POSIX hard-link no-clobber semantics")
    def test_two_real_export_processes_compete_for_same_output_only_one_succeeds(self):
        target = self.home / "race.jsonl"
        args = argv_for(self.db, target, self.head, "normal")
        env = self.environment()
        processes = [
            subprocess.Popen(
                args, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
            ) for _ in range(2)
        ]
        results = [p.communicate(timeout=90) for p in processes]
        exits = [p.returncode for p in processes]
        self.assertEqual(sorted(e == 0 for e in exits), [False, True],
                         [(e, err[-400:]) for e, (_, err) in zip(exits, results)])
        self.assert_complete_jsonl(target)
        self.assert_original_unchanged()
        self.assertEqual(list(self.home.glob(".mojum-private-trace-*.jsonl")), [])

    def test_advancing_real_source_after_head_capture_never_publishes(self):
        target = self.home / "stale-anchor.jsonl"
        options = dict(
            allow_private_export=True, expected_journal_head=self.head,
            exploration_mode="goal_free", developmental_policy="local_cell_v4",
            journal_backend="segmented", journal_segment_size=64,
            sensory_mode="none", neural_mode="disabled",
        )
        # This is a controlled real write before the snapshot, not modifying
        # or corrupting the source behind the exporter's back.
        with Aquarium(
            self.db, SEED, GRAPH, neural_mode="disabled",
            exploration_mode="goal_free", developmental_policy="local_cell_v4",
            sensory_mode="none", journal_backend="segmented",
            journal_segment_size=64,
        ) as live:
            live.step()
            current = live.checkpoint()["journal_head"]
        self.assertNotEqual(current, self.head)
        newer_original = self.db.read_bytes()
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            export_private_trace(self.db, target, SEED, GRAPH, **options)
        self.assertFalse(target.exists())
        self.assertEqual(self.db.read_bytes(), newer_original)
        with Aquarium(self.db, SEED, GRAPH,
                      neural_mode="disabled", exploration_mode="goal_free",
                      developmental_policy="local_cell_v4",
                      sensory_mode="none", journal_backend="segmented",
                      journal_segment_size=64,
                      external_expected_head=current) as check:
            self.assertEqual(check.store.count(), 7)
        self.assertEqual(self.db.read_bytes(), newer_original)


    def test_corrupt_boundary_only_in_staged_backup_never_exports(self):
        # Create two real logical segments (segment size 64) with the
        # canonical emitter, then alter ONLY a disposable read-only source
        # backup. The original must remain independently replayable.
        with Aquarium(
            self.db, SEED, GRAPH, neural_mode="disabled",
            exploration_mode="goal_free", developmental_policy="local_cell_v4",
            sensory_mode="none", journal_backend="segmented",
            journal_segment_size=64, external_expected_head=self.head,
        ) as producer:
            for _ in range(60):
                producer.step()
            trusted_head = producer.checkpoint()["journal_head"]
            self.assertEqual(producer.store.count(), 66)
            self.assertEqual(producer.store.verify_chain(
                external_expected_head=trusted_head
            )["count"], 66)
        source_before = self.db.read_bytes()
        from mojum import aquarium_trace_export as exporter
        from contextlib import closing
        original_backup = exporter._stage_sqlite

        def corrupt_copy(source, stage):
            original_backup(source, stage)
            with closing(sqlite3.connect(str(stage))) as connection:
                with connection:
                    connection.execute("DROP TRIGGER no_boundary_update")
                    connection.execute(
                        "UPDATE segments SET prior_segment_head=? "
                        "WHERE segment_index=1", ("f" * 64,),
                    )
                    self.assertEqual(connection.total_changes, 1)

        target = self.home / "forged-boundary.jsonl"
        with patch("mojum.aquarium_trace_export._stage_sqlite",
                   side_effect=corrupt_copy):
            with self.assertRaises(ValueError):
                export_private_trace(
                    self.db, target, SEED, GRAPH, allow_private_export=True,
                    expected_journal_head=trusted_head,
                    exploration_mode="goal_free",
                    developmental_policy="local_cell_v4",
                    journal_backend="segmented", journal_segment_size=64,
                    sensory_mode="none", neural_mode="disabled",
                )
        self.assertFalse(target.exists(), "forged segment must not publish")
        self.assertEqual(self.db.read_bytes(), source_before)
        with Aquarium(
            self.db, SEED, GRAPH, neural_mode="disabled",
            exploration_mode="goal_free", developmental_policy="local_cell_v4",
            sensory_mode="none", journal_backend="segmented",
            journal_segment_size=64, external_expected_head=trusted_head,
        ) as still_valid:
            self.assertEqual(still_valid.store.count(), 66)
            self.assertEqual(still_valid.checkpoint()["journal_head"], trusted_head)


if __name__ == "__main__":
    unittest.main()
