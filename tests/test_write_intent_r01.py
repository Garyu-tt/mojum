"""Durable prepare→journal→anchor: real SQLite crash boundary QA."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.cold_aquarium import export_quiescent_aquarium
from mojum.genesis import ACQUISITION_SCHEMA
from mojum.guarded_aquarium import GuardedAquarium
from mojum.journal_lineage import inspect_archive
from mojum.local_authority import AuthorityError, LocalAuthority
from mojum.write_intent import (
    IntentGuardedAquarium, install_intent_table, inspect_prepared,
)

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text("utf-8"))
ID = SEED["genome_id"]


def gene(tick, *, event_id="intent-gene"):
    return {
        "schema": ACQUISITION_SCHEMA,
        "event_id": event_id, "genome_id": ID, "gene": "heartbeat_bpm",
        "delta": 7, "valid_from_tick": tick, "observed_at_ns": 0,
        "recorded_at_ns": tick * 10_000_000, "source": "fixture-only",
        "evidence": "synthetic-intent-unit-test",
    }


class WriteIntentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.db = self.dir / "journal.sqlite3"
        self.registry = self.dir / "authority.sqlite3"
        self.archive = self.dir / "origin.json"
        with Aquarium(self.db, SEED, GRAPH) as q:
            q.step(dx=.01)
            manifest = export_quiescent_aquarium(q, self.archive)
        self.baseline = inspect_archive(
            self.archive, expected_organism_id=ID, expected_tick=1,
            expected_manifest_sha256=manifest["manifest_sha256"],
        )
        with LocalAuthority(self.registry, create=True) as r:
            r.provision(self.baseline, "node-a")

    def guard(self, generation=1):
        return IntentGuardedAquarium(
            self.db, self.registry, SEED, GRAPH,
            writer="node-a", epoch=1, generation=generation,
        )

    def read(self):
        return inspect_prepared(
            self.db, self.registry, SEED, GRAPH, organism_id=ID,
        )

    def state(self):
        with LocalAuthority(self.registry) as r:
            return r.state(ID)

    def install(self):
        install_intent_table(self.registry)

    def test_schema_is_explicit_and_missing_schema_does_not_write(self):
        with self.assertRaisesRegex(AuthorityError, "explicitly installed"):
            self.guard()
        self.assertEqual(self.state()["event_count"], 2)
        self.install()
        self.install()
        with self.guard() as writer:
            self.assertEqual(writer.checkpoint()["tick"], 1)
        self.assertEqual(self.read()["classification"], "NO_PREPARED_INTENT")

    def test_normal_step_is_prepared_committed_finalized(self):
        self.install()
        with self.guard() as writer:
            frame = writer.step(dx=.035)
            self.assertEqual(frame["tick"], 1)
            self.assertEqual(writer.generation, 2)
            self.assertEqual(writer.checkpoint()["tick"], 2)
            writer.step(dx=-.02)
            self.assertEqual(writer.generation, 3)
        state = self.state()
        self.assertEqual(state["phase"], "ACTIVE")
        self.assertEqual(state["event_count"], 4)
        self.assertEqual(state["generation"], 3)
        report = self.read()
        self.assertEqual(report["classification"], "NO_PREPARED_INTENT")
        self.assertFalse(report["auto_repair_allowed"])
        with self.guard(generation=3) as reopened:
            self.assertEqual(reopened.checkpoint()["journal_head"], state["head"])

    def test_gene_acquisition_is_exactly_prepared_and_does_not_advance_tick(self):
        self.install()
        with self.guard() as q:
            revision = q.acquire(gene(1))
            self.assertEqual(revision, 1)
            self.assertEqual(q.generation, 2)
            self.assertEqual(q.checkpoint()["tick"], 1)
            q.step(dx=0)
            self.assertEqual(q.generation, 3)
        with Aquarium(self.db, SEED, GRAPH) as view:
            self.assertEqual(view.tick, 2)
            self.assertEqual(view.store.count(), 4)
            self.assertEqual(view.checkpoint()["journal_head"], self.state()["head"])

    def test_invalid_movement_does_not_prepare_or_poison(self):
        self.install()
        with self.guard() as q:
            for dx in ("0.1", True, float("inf")):
                with self.subTest(dx=dx), self.assertRaises(ValueError):
                    q.step(dx=dx)
                self.assertEqual(self.state()["phase"], "ACTIVE")
            q.step(dx=0.01)
        self.assertEqual(self.state()["generation"], 2)
        self.assertEqual(self.read()["classification"], "NO_PREPARED_INTENT")

    def test_invalid_acquired_gene_does_not_prepare(self):
        self.install()
        with self.guard() as q:
            bad = gene(1)
            bad["valid_from_tick"] = 0
            with self.assertRaises(ValueError):
                q.acquire(bad)
            q.step(dx=.02)
            self.assertEqual(q.generation, 2)
        self.assertEqual(self.read()["classification"], "NO_PREPARED_INTENT")

    def test_crash_after_durable_prepare_before_any_event_remains_frozen(self):
        self.install()
        with self.guard() as writer:
            with patch.object(writer._aq, "step", side_effect=SystemExit(31)):
                with self.assertRaises(SystemExit):
                    writer.step(dx=.02)
        row = self.state()
        self.assertEqual(row["phase"], "FROZEN")
        self.assertEqual(row["event_count"], 2)
        result = self.read()
        self.assertEqual(result["classification"], "PREPARED_NO_EVENT_STOP")
        self.assertFalse(result["auto_repair_allowed"])
        with self.assertRaises(AuthorityError):
            self.guard()
        with self.assertRaises(AuthorityError):
            GuardedAquarium(
                self.db, self.registry, SEED, GRAPH,
                writer="node-a", epoch=1, generation=1,
            )

    def test_crash_after_one_committed_frame_before_anchor_records_exact_intent(self):
        self.install()
        with self.guard() as writer:
            with patch.object(writer, "_finish", side_effect=OSError("crash after append")):
                with self.assertRaises(OSError):
                    writer.step(dx=.037)
        result = self.read()
        self.assertEqual(result["classification"],
                         "PREPARED_EXACT_EVENT_REQUIRES_REVIEW")
        self.assertEqual(result["journal"]["event_count"], 3)
        self.assertEqual(result["authority"]["event_count"], 2)
        self.assertEqual(result["journal"]["head"], result["intent"]["expected_head"])
        self.assertFalse(result["auto_repair_allowed"])
        self.assertFalse(result["automatic_writer_promotion_allowed"])
        self.assertEqual(self.state()["generation"], 1)
        self.assertEqual(self.state()["phase"], "FROZEN")
        with self.assertRaises(AuthorityError):
            self.guard()

    def test_matching_extra_acquisition_also_needs_independent_review(self):
        self.install()
        with self.guard() as q:
            with patch.object(q, "_finish", side_effect=OSError("post append crash")):
                with self.assertRaises(OSError):
                    q.acquire(gene(1))
        result = self.read()
        self.assertEqual(result["classification"],
                         "PREPARED_EXACT_EVENT_REQUIRES_REVIEW")
        self.assertEqual(result["journal"]["event_count"], 3)
        self.assertEqual(result["intent"]["expected_tick"], 1)
        self.assertEqual(self.state()["tick"], 1)

    def test_unexpected_direct_writer_during_prepared_state_is_detected(self):
        self.install()
        with self.guard() as q:
            with patch.object(q._aq, "step", side_effect=OSError("before append")):
                with self.assertRaises(OSError):
                    q.step(dx=.09)
        with Aquarium(self.db, SEED, GRAPH) as bypass:
            bypass.step(dx=-.07)
        result = self.read()
        self.assertEqual(result["classification"], "PREPARED_MISMATCH_STOP")
        self.assertEqual(result["journal"]["event_count"], 3)
        self.assertFalse(result["auto_repair_allowed"])
        self.assertEqual(self.state()["head"], self.baseline.head)

    def test_transfer_cannot_activate_over_prepared_mutation(self):
        self.install()
        with self.guard() as q:
            with patch.object(q._aq, "step", side_effect=OSError("crash")):
                with self.assertRaises(OSError):
                    q.step(dx=.03)
        with LocalAuthority(self.registry) as authority:
            with self.assertRaises(AuthorityError):
                authority.freeze_transfer(
                    self.baseline, self.baseline,
                    source_writer="node-a", target_writer="node-b",
                    epoch=1, generation=1,
                )
            with self.assertRaises(AuthorityError):
                authority.activate_transfer(
                    self.baseline, target_writer="node-b",
                    prior_epoch=1, frozen_generation=1,
                )
        self.assertEqual(self.state()["phase"], "FROZEN")

    def test_invalid_existing_sqlite_fails_closed_for_intent_audit(self):
        self.install()
        with self.guard() as writer:
            with patch.object(writer._aq, "step", side_effect=OSError("crash")):
                with self.assertRaises(OSError):
                    writer.step(dx=.025)
        with sqlite3.connect(self.db) as db:
            db.execute("DROP TRIGGER noosphere_no_update")
            db.execute("UPDATE events SET wire_json=? WHERE seq=2", ("{}",))
        report = self.read()
        self.assertEqual(report["classification"], "JOURNAL_INVALID_STOP")
        self.assertEqual(self.state()["phase"], "FROZEN")

    def test_cli_has_explicit_install_and_exit_three_on_pending(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        base = [sys.executable, "-m", "mojum.write_intent"]
        installed = subprocess.run(
            [*base, "install", "--registry", str(self.registry)],
            text=True, capture_output=True, env=env,
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)
        inspect = [*base, "inspect", "--journal", str(self.db),
                   "--registry", str(self.registry), "--organism-id", ID,
                   "--genome", str(ROOT / "examples/genesis_seed.json"),
                   "--graph", str(ROOT / "examples/first_loop.json")]
        clear = subprocess.run(inspect, text=True, capture_output=True, env=env)
        self.assertEqual(clear.returncode, 0, clear.stderr)
        self.assertEqual(json.loads(clear.stdout)["classification"], "NO_PREPARED_INTENT")
        with self.guard() as q:
            with patch.object(q._aq, "step", side_effect=SystemExit(2)):
                with self.assertRaises(SystemExit):
                    q.step(dx=.02)
        pending = subprocess.run(inspect, text=True, capture_output=True, env=env)
        self.assertEqual(pending.returncode, 3, pending.stderr)
        self.assertEqual(json.loads(pending.stdout)["classification"],
                         "PREPARED_NO_EVENT_STOP")

    def test_report_excludes_private_event_payload_and_cleans_scratch(self):
        self.install()
        with self.guard() as q:
            with patch.object(q, "_finish", side_effect=RuntimeError("crash")):
                with self.assertRaises(RuntimeError):
                    q.step(dx=.031)
        result = self.read()
        printed = json.dumps(result, sort_keys=True)
        for sensitive in ("interoception", "PRIVATE", "acquisition",
                          "movement_request", "heartbeat_bpm"):
            self.assertNotIn(sensitive, printed)
        self.assertFalse(any(x.name.startswith(".mojum-intent-")
                             for x in self.dir.iterdir()))

    def test_missing_registry_is_never_bootstrapped_by_installer(self):
        missing = self.dir / "missing.sqlite3"
        with self.assertRaises(AuthorityError):
            install_intent_table(missing)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
