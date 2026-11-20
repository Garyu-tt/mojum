"""Single-host writer admission and source-frozen handoff of real cold archives."""
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
from mojum.cold_aquarium import export_quiescent_aquarium, restore_cold_aquarium
from mojum.journal_lineage import inspect_archive
from mojum.local_authority import AuthorityError, LocalAuthority

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text("utf-8"))
ID = SEED["genome_id"]


class LocalAuthorityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = self.root / "authority.sqlite3"
        self.source_db = self.root / "source.sqlite3"
        self.start_archive = self.root / "start.json"
        self.continued_archive = self.root / "next.json"
        with Aquarium(self.source_db, SEED, GRAPH) as src:
            src.step(dx=0.02)
            src.step(dx=0.02)
            self.initial_manifest = export_quiescent_aquarium(src, self.start_archive)
        self.base = self.evidence(self.start_archive, 2)

    def evidence(self, path, tick):
        return inspect_archive(
            path, expected_organism_id=ID, expected_tick=tick,
        )

    def authority(self):
        return LocalAuthority(self.registry, create=True)

    def provision(self, writer="node-a"):
        store = self.authority()
        state = store.provision(self.base, writer)
        self.assertEqual(state["phase"], "ACTIVE")
        self.assertEqual(state["epoch"], 1)
        self.assertEqual(state["generation"], 1)
        return store

    def next_snapshot(self, dx=0.015):
        with Aquarium(self.source_db, SEED, GRAPH) as q:
            q.step(dx=dx)
            export_quiescent_aquarium(q, self.continued_archive)
        return self.evidence(self.continued_archive, 3)

    def test_provision_is_explicit_and_durable_after_reopen(self):
        with self.provision() as registry:
            a = registry.admit(self.base, writer="node-a", epoch=1, generation=1)
            self.assertEqual(a["status"], "LOCAL_ADMISSION_ONLY_NOT_DISTRIBUTED_AUTHORITY")
            self.assertEqual(a["head"], self.base.head)
        with LocalAuthority(self.registry) as reopened:
            self.assertEqual(reopened.state(ID)["writer"], "node-a")
            self.assertEqual(reopened.state(ID)["event_count"], 3)
            self.assertEqual(reopened.admit(
                self.base, writer="node-a", epoch=1, generation=1,
            )["epoch"], 1)

    def test_second_connection_observes_frozen_and_new_epoch(self):
        with self.provision() as owner, LocalAuthority(self.registry) as observer:
            frozen = owner.freeze_transfer(
                self.base, self.base, source_writer="node-a",
                target_writer="node-b", epoch=1, generation=1,
            )
            self.assertEqual(frozen["phase"], "FROZEN")
            self.assertEqual(frozen["generation"], 2)
            for writer in ("node-a", "node-b"):
                with self.subTest(writer=writer), self.assertRaises(AuthorityError):
                    observer.admit(self.base, writer=writer,
                                   epoch=1, generation=2)
            after = observer.activate_transfer(
                self.base, target_writer="node-b",
                prior_epoch=1, frozen_generation=2,
            )
            self.assertEqual(after["phase"], "ACTIVE")
            self.assertEqual(after["writer"], "node-b")
            self.assertEqual(after["epoch"], 2)
            self.assertEqual(after["generation"], 3)
            with self.assertRaises(AuthorityError):
                owner.admit(self.base, writer="node-a", epoch=1, generation=1)
            self.assertEqual(owner.admit(
                self.base, writer="node-b", epoch=2, generation=3,
            )["epoch"], 2)

    def test_frozen_state_has_no_automatic_timeout_regrant(self):
        with self.provision() as store:
            store.freeze_transfer(
                self.base, self.base, source_writer="node-a",
                target_writer="node-b", epoch=1, generation=1,
            )
        with LocalAuthority(self.registry) as reopened:
            self.assertEqual(reopened.state(ID)["phase"], "FROZEN")
            with self.assertRaises(AuthorityError):
                reopened.admit(self.base, writer="node-a", epoch=1, generation=2)
            with self.assertRaises(AuthorityError):
                reopened.activate_transfer(self.base, target_writer="node-c",
                                           prior_epoch=1, frozen_generation=2)
            self.assertEqual(reopened.state(ID)["phase"], "FROZEN")

    def test_generation_cas_refuses_duplicate_or_wrong_transfer(self):
        with self.provision() as s:
            with self.assertRaises(AuthorityError):
                s.freeze_transfer(
                    self.base, self.base, source_writer="node-a",
                    target_writer="node-a", epoch=1, generation=1,
                )
            s.freeze_transfer(
                self.base, self.base, source_writer="node-a",
                target_writer="node-b", epoch=1, generation=1,
            )
            with self.assertRaises(AuthorityError):
                s.freeze_transfer(
                    self.base, self.base, source_writer="node-a",
                    target_writer="node-b", epoch=1, generation=1,
                )
            for epoch, generation in ((1, 1), (2, 2), (1, 4)):
                with self.subTest(epoch=epoch, generation=generation):
                    with self.assertRaises(AuthorityError):
                        s.activate_transfer(
                            self.base, target_writer="node-b",
                            prior_epoch=epoch, frozen_generation=generation,
                        )
            s.activate_transfer(self.base, target_writer="node-b",
                                prior_epoch=1, frozen_generation=2)
            with self.assertRaises(AuthorityError):
                s.activate_transfer(self.base, target_writer="node-b",
                                    prior_epoch=1, frozen_generation=2)

    def test_progress_requires_strict_common_prefix(self):
        current = self.next_snapshot()
        with self.provision() as s:
            with self.assertRaises(AuthorityError):
                s.progress(self.base, writer="node-a", epoch=1, generation=1)
            state = s.progress(current, writer="node-a", epoch=1, generation=1)
            self.assertEqual(state["head"], current.head)
            self.assertEqual(state["event_count"], 4)
            self.assertEqual(state["tick"], 3)
            self.assertEqual(state["generation"], 2)
            with self.assertRaises(AuthorityError):
                s.admit(self.base, writer="node-a", epoch=1, generation=1)
            with self.assertRaises(AuthorityError):
                s.progress(current, writer="node-a", epoch=1, generation=1)
            with self.assertRaises(AuthorityError):
                s.progress(self.base, writer="node-a", epoch=1, generation=2)
            self.assertEqual(s.admit(
                current, writer="node-a", epoch=1, generation=2,
            )["head"], current.head)

    def test_diverged_copy_cannot_be_used_for_handoff_or_progress(self):
        alt_db = self.root / "alternate.sqlite3"
        alt_archive = self.root / "alternate.json"
        restore_cold_aquarium(
            self.start_archive, alt_db, expected_organism_id=ID,
            expected_tick=2,
        )
        with Aquarium(alt_db, SEED, GRAPH) as fork:
            fork.step(dx=-0.02)
            export_quiescent_aquarium(fork, alt_archive)
        right = self.evidence(alt_archive, 3)
        left = self.next_snapshot(dx=0.03)
        self.assertNotEqual(right.head, left.head)
        with self.provision() as s:
            state = s.progress(left, writer="node-a", epoch=1, generation=1)
            self.assertEqual(state["head"], left.head)
            with self.assertRaisesRegex(AuthorityError, "trusted prefix"):
                s.progress(right, writer="node-a", epoch=1, generation=2)
            with self.assertRaises(AuthorityError):
                s.freeze_transfer(
                    left, right, source_writer="node-a",
                    target_writer="node-b", epoch=1, generation=2,
                )
            self.assertEqual(s.state(ID)["phase"], "ACTIVE")

    def test_wrong_target_root_does_not_revoke_current_writer(self):
        changed = copy.deepcopy(SEED)
        changed["inherited"]["heartbeat_bpm"] = 65
        foreign_db = self.root / "foreign.sqlite3"
        foreign_archive = self.root / "foreign.json"
        with Aquarium(foreign_db, changed, GRAPH) as q:
            q.step()
            q.step()
            export_quiescent_aquarium(q, foreign_archive)
        foreign = self.evidence(foreign_archive, 2)
        with self.provision() as store:
            with self.assertRaises(AuthorityError):
                store.freeze_transfer(
                    self.base, foreign, source_writer="node-a",
                    target_writer="node-b", epoch=1, generation=1,
                )
            self.assertEqual(store.state(ID)["phase"], "ACTIVE")

    def test_registry_missing_does_not_auto_bootstrap(self):
        with self.assertRaises(AuthorityError):
            LocalAuthority(self.registry)
        self.assertFalse(self.registry.exists())

    def test_stale_writer_epoch_and_invalid_ids_are_rejected(self):
        with self.provision() as s:
            for writer, epoch, generation in (
                ("node-b", 1, 1), ("node-a", 2, 1), ("node-a", 1, 2),
                ("node-a", True, 1), ("node-a", 1, False),
                ("node\nlog", 1, 1), ("пиксель", 1, 1),
            ):
                with self.subTest(writer=writer, epoch=epoch,
                                  generation=generation), self.assertRaises(AuthorityError):
                    s.admit(self.base, writer=writer, epoch=epoch,
                            generation=generation)
            with self.assertRaises(AuthorityError):
                s.provision(self.base, "node-b")

    def test_cli_three_process_handoff_and_denial(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        command = [sys.executable, "-m", "mojum.local_authority"]
        fixed = ["--registry", str(self.registry),
                 "--organism-id", ID]
        source = ["--archive", str(self.start_archive), "--tick", "2",
                  "--manifest-sha256", self.initial_manifest["manifest_sha256"]]
        prov = subprocess.run(
            [*command, "provision", *fixed, *source, "--writer", "node-a"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(prov.returncode, 0, prov.stderr)
        freeze = subprocess.run(
            [*command, "freeze", *fixed, *source,
             "--target-archive", str(self.start_archive),
             "--target-manifest-sha256", self.initial_manifest["manifest_sha256"],
             "--source-writer", "node-a", "--target-writer", "node-b",
             "--epoch", "1", "--generation", "1"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(freeze.returncode, 0, freeze.stderr)
        self.assertEqual(json.loads(freeze.stdout)["phase"], "FROZEN")
        refused = subprocess.run(
            [*command, "admit", *fixed, *source, "--writer", "node-a",
             "--epoch", "1", "--generation", "2"],
            capture_output=True, text=True, env=env,
        )
        self.assertNotEqual(refused.returncode, 0)
        activated = subprocess.run(
            [*command, "activate", *fixed, *source, "--target-writer", "node-b",
             "--prior-epoch", "1", "--frozen-generation", "2"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(activated.returncode, 0, activated.stderr)
        self.assertEqual(json.loads(activated.stdout)["epoch"], 2)
        accepted = subprocess.run(
            [*command, "admit", *fixed, *source, "--writer", "node-b",
             "--epoch", "2", "--generation", "3"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)["writer"], "node-b")
        prior = subprocess.run(
            [*command, "admit", *fixed, *source, "--writer", "node-a",
             "--epoch", "1", "--generation", "1"],
            capture_output=True, text=True, env=env,
        )
        self.assertNotEqual(prior.returncode, 0)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions only")
    def test_registry_file_is_private_and_existing_not_overwritten(self):
        with self.provision() as s:
            self.assertEqual(self.registry.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                LocalAuthority(self.registry, create=True)
            self.assertEqual(s.state(ID)["epoch"], 1)


if __name__ == "__main__":
    unittest.main()
