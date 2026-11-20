"""Real cold Aquarium history ancestry and fork safety, without a mesh."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.checkpoint_bundle import dumps, pack, unpack
from mojum.cold_aquarium import export_quiescent_aquarium, restore_cold_aquarium
from mojum.genesis import ACQUISITION_SCHEMA
from mojum.journal_lineage import audit_archives, inspect_archive

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples" / "genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads((ROOT / "examples" / "first_loop.json").read_text("utf-8"))
ID = SEED["genome_id"]


class JournalLineageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "source.sqlite3"
        self.baseline = self.root / "baseline.json"
        self.left_db = self.root / "left.sqlite3"
        self.right_db = self.root / "right.sqlite3"
        self.left_archive = self.root / "left.json"
        self.right_archive = self.root / "right.json"

    def _baseline(self, count=3):
        with Aquarium(self.source, SEED, GRAPH) as q:
            for _ in range(count):
                q.step(dx=.02)
            return export_quiescent_aquarium(q, self.baseline)

    def _replicas(self, count=3):
        manifest = self._baseline(count)
        for dest in (self.left_db, self.right_db):
            restore_cold_aquarium(
                self.baseline, dest, expected_organism_id=ID,
                expected_tick=count,
                expected_manifest_sha256=manifest["manifest_sha256"],
            )
        return manifest

    def _audit(self, lt, rt, **kw):
        return audit_archives(
            self.left_archive, self.right_archive,
            expected_organism_id=ID, left_tick=lt, right_tick=rt, **kw,
        )

    def _divergent_frames(self):
        self._replicas()
        with Aquarium(self.left_db, SEED, GRAPH) as left:
            left.step(dx=.01)
            a = export_quiescent_aquarium(left, self.left_archive)
        with Aquarium(self.right_db, SEED, GRAPH) as right:
            right.step(dx=-.02)
            b = export_quiescent_aquarium(right, self.right_archive)
        return a, b

    def test_identical_two_valid_archives(self):
        trusted = self._baseline()
        shutil.copyfile(self.baseline, self.left_archive)
        shutil.copyfile(self.baseline, self.right_archive)
        result = self._audit(
            3, 3, left_manifest_sha256=trusted["manifest_sha256"],
            right_manifest_sha256=trusted["manifest_sha256"],
        )
        self.assertEqual(result["classification"], "IDENTICAL_HISTORY")
        self.assertEqual(result["common_events"], 4)
        self.assertFalse(result["requires_conflict_resolution"])
        self.assertFalse(result["automatic_writer_promotion_allowed"])

    def test_source_history_is_ancestor_of_continuation(self):
        self._baseline()
        shutil.copyfile(self.baseline, self.left_archive)
        with Aquarium(self.source, SEED, GRAPH) as q:
            q.step(dx=.015)
            q.step(dx=.015)
            export_quiescent_aquarium(q, self.right_archive)
        result = self._audit(3, 5)
        self.assertEqual(result["classification"], "LEFT_ANCESTOR")
        self.assertEqual(result["common_events"], 4)
        self.assertEqual(result["right"]["events"], 6)
        self.assertFalse(result["automatic_writer_promotion_allowed"])
        result_rev = audit_archives(
            self.right_archive, self.left_archive,
            expected_organism_id=ID, left_tick=5, right_tick=3,
        )
        self.assertEqual(result_rev["classification"], "RIGHT_ANCESTOR")

    def test_same_epoch_same_tick_two_real_forks_detected(self):
        a, b = self._divergent_frames()
        result = self._audit(
            4, 4, left_manifest_sha256=a["manifest_sha256"],
            right_manifest_sha256=b["manifest_sha256"],
        )
        self.assertEqual(result["classification"], "FORK_DETECTED_STOP")
        self.assertEqual(result["common_events"], 4)
        self.assertNotEqual(result["left"]["head"], result["right"]["head"])
        self.assertTrue(result["requires_conflict_resolution"])
        self.assertFalse(result["automatic_writer_promotion_allowed"])

    def test_divergent_acquired_gene_without_advancing_tick_is_a_fork(self):
        self._replicas(1)
        for db, output, delta in (
            (self.left_db, self.left_archive, 10),
            (self.right_db, self.right_archive, 15),
        ):
            with Aquarium(db, SEED, GRAPH) as q:
                event = {
                    "schema": ACQUISITION_SCHEMA,
                    "event_id": "forked-gene",
                    "genome_id": ID, "gene": "heartbeat_bpm", "delta": delta,
                    "valid_from_tick": q.tick,
                    "observed_at_ns": 0, "recorded_at_ns": q.tick * q.dt_ns,
                    "source": "synthetic-lineage-test", "evidence": "fixture-only",
                }
                q.acquire(event)
                export_quiescent_aquarium(q, output)
        result = self._audit(1, 1)
        self.assertEqual(result["classification"], "FORK_DETECTED_STOP")
        self.assertEqual(result["common_events"], 2)
        self.assertEqual(result["left"]["tick"], 1)
        self.assertEqual(result["right"]["tick"], 1)

    def test_same_id_but_unrelated_bootstraps_are_not_merged(self):
        self._baseline(1)
        shutil.copyfile(self.baseline, self.left_archive)
        changed_seed = copy.deepcopy(SEED)
        changed_seed["inherited"]["heartbeat_bpm"] = 65
        with Aquarium(self.right_db, changed_seed, GRAPH) as other:
            other.step(dx=.02)
            export_quiescent_aquarium(other, self.right_archive)
        result = self._audit(1, 1)
        self.assertEqual(result["classification"], "UNRELATED_ROOT_STOP")
        self.assertEqual(result["common_events"], 0)
        self.assertIsNone(result["last_common_head"])
        self.assertTrue(result["requires_conflict_resolution"])

    def test_wrong_identity_tick_and_independent_manifest_fail_closed(self):
        trusted = self._baseline(2)
        for args in (
            {"expected_organism_id": "different", "expected_tick": 2},
            {"expected_organism_id": ID, "expected_tick": 3},
            {"expected_organism_id": ID, "expected_tick": 2,
             "expected_manifest_sha256": "f" * 64
             if trusted["manifest_sha256"] != "f" * 64 else "0" * 64},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                inspect_archive(self.baseline, **args)

    def test_corrupt_noosphere_event_chain_rejected_even_if_bundle_rehashed(self):
        self._baseline()
        wire = json.loads(self.baseline.read_text("ascii"))
        parts = unpack(wire, expected_revision=3, expected_organism_id=ID)
        memory = json.loads(parts["memory"])
        memory["items"][2]["event_hash"] = "0" * 64
        modified = dict(parts, memory=json.dumps(
            memory, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8"))
        rebuilt = pack(ID, 3, modified)
        corrupt = self.root / "self-consistent-but-forged.json"
        corrupt.write_text(dumps(rebuilt), encoding="ascii")
        with self.assertRaisesRegex(ValueError, "archived event hash chain mismatch"):
            inspect_archive(corrupt, expected_organism_id=ID, expected_tick=3)

    def test_corrupt_noosphere_event_sequence_rejected_even_if_bundle_rehashed(self):
        self._baseline()
        wire = json.loads(self.baseline.read_text("ascii"))
        parts = unpack(wire, expected_revision=3, expected_organism_id=ID)
        memory = json.loads(parts["memory"])
        memory["items"][1]["seq"] = 10
        modified = dict(parts, memory=json.dumps(
            memory, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8"))
        rebuilt = pack(ID, 3, modified)
        corrupt = self.root / "bad-sequence.json"
        corrupt.write_text(dumps(rebuilt), encoding="ascii")
        with self.assertRaisesRegex(ValueError, "entry or sequence"):
            inspect_archive(corrupt, expected_organism_id=ID, expected_tick=3)

    def test_report_has_no_private_event_payloads(self):
        self._divergent_frames()
        result = self._audit(4, 4)
        text = json.dumps(result, sort_keys=True)
        self.assertNotIn("movement_request", text)
        self.assertNotIn("interoception", text)
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("acquisition", text)
        self.assertIn("FORK_DETECTED_STOP", text)

    def test_cli_returns_nonzero_for_fork_but_zero_for_valid_ancestor(self):
        self._divergent_frames()
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        cmd = [sys.executable, "-m", "mojum.journal_lineage",
               "--left-archive", str(self.left_archive),
               "--right-archive", str(self.right_archive),
               "--organism-id", ID, "--left-tick", "4", "--right-tick", "4"]
        fork = subprocess.run(cmd, env=env, text=True, capture_output=True)
        self.assertEqual(fork.returncode, 3, fork.stderr)
        self.assertEqual(json.loads(fork.stdout)["classification"],
                         "FORK_DETECTED_STOP")
        ancestor = subprocess.run(
            [*cmd[:],], env=env, text=True, capture_output=True
        )
        self.assertEqual(ancestor.returncode, 3)
        # Comparison with its own exact history does not signal a conflict.
        same_cmd = list(cmd)
        same_cmd[same_cmd.index("--right-archive") + 1] = str(self.left_archive)
        same = subprocess.run(same_cmd, env=env, text=True, capture_output=True)
        self.assertEqual(same.returncode, 0, same.stderr)
        self.assertEqual(json.loads(same.stdout)["classification"],
                         "IDENTICAL_HISTORY")


if __name__ == "__main__":
    unittest.main()
