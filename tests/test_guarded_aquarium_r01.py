"""One trusted registry gates real synthetic Aquarium frame/gene appends."""
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
from mojum.cold_aquarium import export_quiescent_aquarium
from mojum.genesis import ACQUISITION_SCHEMA
from mojum.guarded_aquarium import GuardedAquarium
from mojum.journal_lineage import inspect_archive
from mojum.local_authority import AuthorityError, LocalAuthority

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text("utf-8"))
ID = SEED["genome_id"]


def gene(tick, event_id="guarded-gene"):
    return {
        "schema": ACQUISITION_SCHEMA,
        "event_id": event_id, "genome_id": ID, "gene": "heartbeat_bpm",
        "delta": 10, "valid_from_tick": tick,
        "observed_at_ns": 0,
        "recorded_at_ns": tick * 10_000_000,
        "source": "synthetic-guarded-writer-fixture",
        "evidence": "local-only",
    }


class GuardedAquariumTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "aquarium.sqlite3"
        self.registry = self.root / "registry.sqlite3"
        self.origin = self.root / "origin.json"
        with Aquarium(self.db, SEED, GRAPH) as q:
            for _ in range(2):
                q.step()
            self.manifest = export_quiescent_aquarium(q, self.origin)
        self.source = inspect_archive(
            self.origin, expected_organism_id=ID, expected_tick=2,
            expected_manifest_sha256=self.manifest["manifest_sha256"],
        )
        with LocalAuthority(self.registry, create=True) as registry:
            registry.provision(self.source, "node-a")

    def guarded(self, *, writer="node-a", epoch=1, generation=1, db=None):
        return GuardedAquarium(
            db or self.db, self.registry, SEED, GRAPH,
            writer=writer, epoch=epoch, generation=generation,
        )

    def state(self):
        with LocalAuthority(self.registry) as reg:
            return reg.state(ID)

    def test_step_automatically_updates_durable_head_and_generation(self):
        with self.guarded() as q:
            before = q.checkpoint()
            result = q.step(dx=.03)
            self.assertEqual(result["tick"], 2)
            self.assertEqual(q.generation, 2)
            after = q.checkpoint()
            self.assertEqual(after["tick"], 3)
            self.assertNotEqual(before["journal_head"], after["journal_head"])
            self.assertEqual(self.state()["head"], after["journal_head"])
            self.assertEqual(self.state()["event_count"], 4)
            q.step(dx=.04)
            self.assertEqual(q.generation, 3)
        with self.guarded(generation=3) as reopened:
            self.assertEqual(reopened.checkpoint()["tick"], 4)

    def test_acquire_does_not_advance_tick_but_updates_authority_event_count(self):
        with self.guarded() as q:
            q.acquire(gene(2))
            self.assertEqual(q.checkpoint()["tick"], 2)
            self.assertEqual(q.generation, 2)
            self.assertEqual(self.state()["event_count"], 4)
            q.step(dx=0)
            self.assertEqual(q.checkpoint()["tick"], 3)
            self.assertEqual(self.state()["event_count"], 5)
        with self.guarded(generation=3) as resumed:
            self.assertEqual(resumed.checkpoint()["tick"], 3)

    def test_second_instance_is_stale_after_first_advances(self):
        with self.guarded() as a, self.guarded() as b:
            a.step(dx=.02)
            snapshot = a.checkpoint()
            with self.assertRaisesRegex(AuthorityError, "no longer admitted"):
                b.step(dx=.02)
            with self.assertRaises(AuthorityError):
                b.checkpoint()
            self.assertEqual(self.state()["head"], snapshot["journal_head"])
            self.assertEqual(self.state()["event_count"], 4)

    def test_frozen_transfer_blocks_source_then_allows_named_target(self):
        copied = self.root / "transfer.json"
        with self.guarded() as source:
            source.step(dx=.01)
            stamped = source.export_cold(copied)
            current = inspect_archive(
                copied, expected_organism_id=ID, expected_tick=3,
                expected_manifest_sha256=stamped["manifest_sha256"],
            )
            with LocalAuthority(self.registry) as control:
                freeze = control.freeze_transfer(
                    current, current, source_writer="node-a",
                    target_writer="node-b", epoch=1, generation=2,
                )
                self.assertEqual(freeze["phase"], "FROZEN")
            with self.assertRaises(AuthorityError):
                source.step()
            self.assertEqual(self.state()["event_count"], 4)
            with LocalAuthority(self.registry) as control:
                activation = control.activate_transfer(
                    current, target_writer="node-b", prior_epoch=1,
                    frozen_generation=3,
                )
                self.assertEqual(activation["epoch"], 2)
            with self.assertRaises(AuthorityError):
                source.step()
        with self.guarded(writer="node-b", epoch=2, generation=4) as receiver:
            receiver.step(dx=.06)
            self.assertEqual(self.state()["writer"], "node-b")
            self.assertEqual(receiver.checkpoint()["tick"], 4)

    def test_missing_registry_fails_before_journal_bootstrap(self):
        missing = self.root / "absent.sqlite3"
        with self.assertRaises(AuthorityError):
            GuardedAquarium(self.root / "also-missing.sqlite3", missing,
                            SEED, GRAPH, writer="node-a", epoch=1, generation=1)
        self.assertFalse(missing.exists())
        self.assertFalse((self.root / "also-missing.sqlite3").exists())

    def test_unlinked_registry_fails_closed_before_mutation(self):
        parked = self.root / "authority-parked.sqlite3"
        with self.guarded() as q:
            snapshot = q.checkpoint()
            os.rename(self.registry, parked)
            with self.assertRaises(AuthorityError):
                q.step()
            self.assertEqual(q.generation, 1)
            os.rename(parked, self.registry)
            self.assertEqual(q.checkpoint(), snapshot)
        self.assertEqual(self.state()["event_count"], 3)

    def test_replaced_registry_inode_fails_closed_even_with_same_contents(self):
        backup = self.root / "registry-copy.sqlite3"
        with self.guarded() as q:
            before = q.checkpoint()
            # For a portable, deterministic inode replacement test, copy only
            # the main file; registry is not being modified during this copy.
            with LocalAuthority(self.registry) as control:
                control.state(ID)
            shutil.copy2(self.registry, backup)
            os.replace(backup, self.registry)
            with self.assertRaisesRegex(AuthorityError, "replaced"):
                q.step()
            self.assertEqual(q.generation, 1)
            # Direct Noosphere inspection only for fixture QA, not runtime.
        with Aquarium(self.db, SEED, GRAPH) as unchanged:
            self.assertEqual(unchanged.checkpoint(), before)

    def test_stale_or_wrong_writer_rejected_without_modifying_source(self):
        for kwargs in (
            {"writer": "node-b"},
            {"epoch": 2},
            {"generation": 2},
            {"writer": "bad writer"},
            {"epoch": True},
            {"generation": False},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises((AuthorityError, ValueError)):
                with self.guarded(**kwargs):
                    self.fail("inadmissible handle opened")
        self.assertEqual(self.state()["event_count"], 3)

    def test_failed_anchor_after_committed_journal_poison_and_reopen_refusal(self):
        with self.guarded() as guarded:
            with patch.object(
                guarded, "_anchor_advance",
                side_effect=OSError("injected registry outage after journal append"),
            ):
                with self.assertRaises(AuthorityError):
                    guarded.step(dx=.07)
            with self.assertRaisesRegex(AuthorityError, "partial commit"):
                guarded.step()
        self.assertEqual(self.state()["event_count"], 3)
        self.assertEqual(self.state()["generation"], 1)
        with Aquarium(self.db, SEED, GRAPH) as observed:
            self.assertEqual(observed.tick, 3)
            self.assertEqual(observed.store.count(), 4)
        with self.assertRaisesRegex(AuthorityError, "differs from authority anchor"):
            self.guarded()

    def test_outage_before_journal_append_does_not_modify_events(self):
        with self.guarded() as guarded:
            initial = guarded.checkpoint()
            # A locked registry rejects the gateway before a Noosphere append.
            with LocalAuthority(self.registry) as other:
                other._db.execute("BEGIN IMMEDIATE")
                try:
                    with self.assertRaises(AuthorityError):
                        guarded.step()
                finally:
                    other._db.execute("ROLLBACK")
            self.assertEqual(guarded.checkpoint(), initial)
        self.assertEqual(self.state()["event_count"], 3)

    def test_frozen_registry_on_reopen_is_denied(self):
        with LocalAuthority(self.registry) as control:
            control.freeze_transfer(
                self.source, self.source,
                source_writer="node-a", target_writer="node-b",
                epoch=1, generation=1,
            )
        with self.assertRaisesRegex(AuthorityError, "no longer admitted"):
            self.guarded()
        self.assertEqual(self.state()["phase"], "FROZEN")

    def test_guarded_export_matches_live_committed_registry_anchor(self):
        archive = self.root / "current.json"
        with self.guarded() as g:
            g.acquire(gene(2, "learned-before-export"))
            manifest = g.export_cold(archive)
        evidence = inspect_archive(
            archive, expected_organism_id=ID, expected_tick=2,
            expected_manifest_sha256=manifest["manifest_sha256"],
        )
        self.assertEqual(evidence.head, self.state()["head"])
        self.assertEqual(self.state()["event_count"], len(evidence.event_hashes))

    def test_external_direct_append_is_detected_and_not_silently_admitted(self):
        with self.guarded() as g:
            with Aquarium(self.db, SEED, GRAPH) as bypass:
                bypass.step(dx=.09)
            with self.assertRaisesRegex(AuthorityError, "differs from authority anchor"):
                g.step()
        self.assertEqual(self.state()["event_count"], 3)
        # Direct mutation is NOT prevented by this opt-in gateway. It is
        # intentionally documented as a residual safety limitation.
        with Aquarium(self.db, SEED, GRAPH) as bypassed:
            self.assertEqual(bypassed.store.count(), 4)

    def test_registry_derived_state_remains_private_metadata_only(self):
        with self.guarded() as g:
            g.step()
            report = self.state()
            encoded = json.dumps(report, sort_keys=True)
            self.assertNotIn("interoception", encoded)
            self.assertNotIn("acquisition", encoded)
            self.assertNotIn("PRIVATE", encoded)


if __name__ == "__main__":
    unittest.main()
