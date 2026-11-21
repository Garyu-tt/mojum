"""Crash-gap reconciliation must never adopt arbitrary Noosphere writes."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium
from mojum.cold_aquarium import export_quiescent_aquarium, restore_cold_aquarium
from mojum.genesis import ACQUISITION_SCHEMA
from mojum.guarded_aquarium import GuardedAquarium
from mojum.journal_lineage import inspect_archive
from mojum.local_authority import AuthorityError, LocalAuthority
from mojum.recovery_audit import inspect_recovery
import mojum.recovery_audit as recovery_audit

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text("utf-8"))
ID = SEED["genome_id"]


def acquired(tick):
    return {
        "schema": ACQUISITION_SCHEMA,
        "event_id": "crash-gap-gene",
        "genome_id": ID, "gene": "heartbeat_bpm", "delta": 8,
        "valid_from_tick": tick, "observed_at_ns": 0,
        "recorded_at_ns": tick * 10_000_000,
        "source": "synthetic-recovery-test", "evidence": "fixture-only",
    }


class RecoveryAuditTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "source.sqlite3"
        self.registry = self.root / "authority.sqlite3"
        self.origin = self.root / "origin.json"
        self.replica_db = self.root / "replica.sqlite3"
        self.replica_archive = self.root / "replica.json"
        with Aquarium(self.db, SEED, GRAPH) as q:
            q.step(dx=.01)
            q.step(dx=.01)
            manifest = export_quiescent_aquarium(q, self.origin)
        self.baseline = inspect_archive(
            self.origin, expected_organism_id=ID, expected_tick=2,
            expected_manifest_sha256=manifest["manifest_sha256"],
        )
        with LocalAuthority(self.registry, create=True) as authority:
            authority.provision(self.baseline, "node-a")

    def inspect(self, *, journal=None, seed=None, gen=1, **kwargs):
        return inspect_recovery(
            journal or self.db, self.registry, seed or SEED, GRAPH,
            organism_id=ID, writer="node-a",
            expected_epoch=1, expected_generation=gen, **kwargs,
        )

    def assert_no_auto_repair(self, result):
        self.assertFalse(result["auto_repair_allowed"])
        self.assertNotIn("interoception", json.dumps(result))
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertNotIn("acquisition", json.dumps(result))
        with LocalAuthority(self.registry) as state:
            stored = state.state(ID)
            self.assertEqual(stored["head"], self.baseline.head)
            self.assertEqual(stored["event_count"], 3)
            self.assertEqual(stored["generation"], 1)

    def test_consistent_journal_matches_exact_authority_anchor(self):
        report = self.inspect()
        self.assertEqual(report["classification"], "IN_SYNC")
        self.assertEqual(report["registry"]["head"], report["journal"]["head"])
        self.assertEqual(report["journal"]["tick"], 2)
        self.assertFalse(report["operator_review_required"])
        self.assert_no_auto_repair(report)

    def test_ambiguous_one_extra_frame_after_registry_anchor_failure(self):
        with GuardedAquarium(
            self.db, self.registry, SEED, GRAPH,
            writer="node-a", epoch=1, generation=1,
        ) as q:
            with patch.object(
                q, "_anchor_advance", side_effect=OSError("simulated crash"),
            ):
                with self.assertRaises(AuthorityError):
                    q.step(dx=.035)
        report = self.inspect()
        self.assertEqual(report["classification"], "ONE_EXTRA_EVENT_AMBIGUOUS_STOP")
        self.assertEqual(report["journal"]["event_count"], 4)
        self.assertEqual(report["journal"]["tick"], 3)
        self.assertTrue(report["common_registry_anchor"])
        self.assertTrue(report["operator_review_required"])
        self.assert_no_auto_repair(report)

    def test_one_extra_acquired_gene_has_same_tick_but_is_still_ambiguous(self):
        with Aquarium(self.db, SEED, GRAPH) as bypass:
            bypass.acquire(acquired(2))
        report = self.inspect()
        self.assertEqual(report["classification"], "ONE_EXTRA_EVENT_AMBIGUOUS_STOP")
        self.assertEqual(report["journal"]["tick"], 2)
        self.assertTrue(report["common_registry_anchor"])
        self.assert_no_auto_repair(report)

    def test_multiple_unauthorized_commits_never_reconcile_automatically(self):
        with Aquarium(self.db, SEED, GRAPH) as bypass:
            bypass.step(dx=.04)
            bypass.step(dx=.04)
        report = self.inspect()
        self.assertEqual(report["classification"], "MULTIPLE_EXTRA_EVENTS_STOP")
        self.assertEqual(report["journal"]["event_count"], 5)
        self.assertTrue(report["common_registry_anchor"])
        self.assert_no_auto_repair(report)

    def test_forked_bootstrap_with_same_organism_id_is_not_accepted(self):
        changed = copy.deepcopy(SEED)
        changed["inherited"]["heartbeat_bpm"] = 68
        with Aquarium(self.replica_db, changed, GRAPH) as q:
            q.step(dx=.01)
            q.step(dx=.01)
            q.step(dx=.01)
        report = self.inspect(journal=self.replica_db, seed=changed)
        self.assertEqual(report["classification"], "JOURNAL_FORK_STOP")
        self.assertFalse(report["common_registry_anchor"])
        self.assert_no_auto_repair(report)

    def test_authority_ahead_of_source_is_rollback_or_fork_not_a_repair(self):
        restore_cold_aquarium(
            self.origin, self.replica_db, expected_organism_id=ID,
            expected_tick=2,
        )
        with Aquarium(self.replica_db, SEED, GRAPH) as q:
            q.step(dx=.02)
            export_quiescent_aquarium(q, self.replica_archive)
        later = inspect_archive(
            self.replica_archive, expected_organism_id=ID, expected_tick=3,
        )
        with LocalAuthority(self.registry) as reg:
            reg.progress(later, writer="node-a", epoch=1, generation=1)
        report = self.inspect(gen=2)
        self.assertEqual(report["classification"], "JOURNAL_ROLLBACK_OR_FORK_STOP")
        self.assertEqual(report["registry"]["event_count"], 4)
        self.assertEqual(report["journal"]["event_count"], 3)
        self.assertFalse(report["auto_repair_allowed"])
        with LocalAuthority(self.registry) as state:
            self.assertEqual(state.state(ID)["head"], later.head)

    def _corrupt_fixture_only(self):
        # LocalNoosphere deliberately has an append-only UPDATE trigger.
        # Explicitly dropping it here models a *compromised owner* who has
        # SQLite DDL privileges on a sacrificial test database, not a normal
        # Aquarium write or a proposed repair procedure.
        with sqlite3.connect(self.db) as sql:
            sql.execute("DROP TRIGGER noosphere_no_update")
            sql.execute("UPDATE events SET wire_json=? WHERE seq=2", ("{}",))

    def test_corrupt_but_existing_sqlite_journal_fails_closed(self):
        self._corrupt_fixture_only()
        report = self.inspect()
        self.assertEqual(report["classification"], "JOURNAL_INVALID_STOP")
        self.assertIsNone(report["journal"]["head"])
        self.assert_no_auto_repair(report)

    def test_source_changes_between_backup_and_final_check(self):
        original = recovery_audit._live_status
        called = False

        def dirty(source):
            nonlocal called
            if not called:
                called = True
                # Deliberately bypass the locked authority registry to model
                # a compromised writer UID during the diagnostic interval.
                with Aquarium(self.db, SEED, GRAPH) as bypass:
                    bypass.step(dx=.07)
            return original(source)

        with patch.object(recovery_audit, "_live_status", side_effect=dirty):
            report = self.inspect()
        self.assertEqual(
            report["classification"], "JOURNAL_CHANGED_DURING_AUDIT_STOP",
        )
        self.assert_no_auto_repair(report)

    def test_frozen_authority_never_looks_like_safe_sync(self):
        with LocalAuthority(self.registry) as registry:
            registry.freeze_transfer(
                self.baseline, self.baseline, source_writer="node-a",
                target_writer="node-b", epoch=1, generation=1,
            )
        report = self.inspect(gen=2)
        self.assertEqual(report["classification"], "AUTHORITY_FROZEN_STOP")
        self.assertFalse(report["auto_repair_allowed"])
        with LocalAuthority(self.registry) as authority:
            self.assertEqual(authority.state(ID)["phase"], "FROZEN")

    def test_expected_epoch_writer_generation_mismatch_refused(self):
        with self.assertRaisesRegex(AuthorityError, "authority changed"):
            self.inspect(gen=2)
        with self.assertRaises(AuthorityError):
            inspect_recovery(
                self.db, self.registry, SEED, GRAPH, organism_id=ID,
                writer="node-b", expected_epoch=1, expected_generation=1,
            )
        with self.assertRaises(AuthorityError):
            inspect_recovery(
                self.db, self.registry, SEED, GRAPH, organism_id=ID,
                writer="node-a", expected_epoch=True, expected_generation=1,
            )

    def test_missing_files_are_never_bootstrapped(self):
        missing = self.root / "missing.sqlite3"
        with self.assertRaises(AuthorityError):
            self.inspect(journal=missing)
        self.assertFalse(missing.exists())
        with self.assertRaises(AuthorityError):
            inspect_recovery(
                self.db, missing, SEED, GRAPH, organism_id=ID,
                writer="node-a", expected_epoch=1, expected_generation=1,
            )
        self.assertFalse(missing.exists())

    def test_cli_exit_code_zero_sync_three_gap_and_no_mutations(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        args = [
            sys.executable, "-m", "mojum.recovery_audit",
            "--journal", str(self.db), "--registry", str(self.registry),
            "--organism-id", ID, "--writer", "node-a",
            "--epoch", "1", "--generation", "1",
        ]
        exact = subprocess.run(args, env=env, text=True, capture_output=True)
        self.assertEqual(exact.returncode, 0, exact.stderr)
        self.assertEqual(json.loads(exact.stdout)["classification"], "IN_SYNC")
        with Aquarium(self.db, SEED, GRAPH) as bypass:
            bypass.step(dx=.02)
        drift = subprocess.run(args, env=env, text=True, capture_output=True)
        self.assertEqual(drift.returncode, 3, drift.stderr)
        payload = json.loads(drift.stdout)
        self.assertEqual(payload["classification"], "ONE_EXTRA_EVENT_AMBIGUOUS_STOP")
        self.assert_no_auto_repair(payload)

    def test_no_archive_or_scratch_after_success_and_failure(self):
        self.inspect()
        self.assertFalse(any(x.name.startswith(".mojum-audit-") for x in self.root.iterdir()))
        self._corrupt_fixture_only()
        self.inspect()
        self.assertFalse(any(x.name.startswith(".mojum-audit-") for x in self.root.iterdir()))


if __name__ == "__main__":
    unittest.main()
