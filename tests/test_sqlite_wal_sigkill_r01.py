"""Real SIGKILL + SQLite WAL recovery matrix for the opt-in durable writer.

No physical power-cut or persistence-barrier guarantee is claimed. All
stores are synthetic test-only SQLite databases under a disposable directory.
"""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium
from mojum.cold_aquarium import export_quiescent_aquarium
from mojum.journal_lineage import inspect_archive
from mojum.local_authority import AuthorityError, LocalAuthority
from mojum.write_intent import (
    IntentGuardedAquarium, install_intent_table, inspect_prepared,
)

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text("utf-8"))
ORGANISM = SEED["genome_id"]

# Test-only worker: stages an *actual* durable operation and pauses. Its
# parent sends an external SIGKILL to terminate the whole process while
# handles and WAL transactions are still open. No injection RPC exists.
WORKER = r"""
import json, os, signal, sys
from mojum.write_intent import IntentGuardedAquarium
from mojum.noosphere import _canonical, _sha

stage, journal, registry, seedpath, graphpath = sys.argv[1:]
seed = json.load(open(seedpath, encoding="utf-8"))
graph = json.load(open(graphpath, encoding="utf-8"))
with IntentGuardedAquarium(
    journal, registry, seed, graph,
    writer="node-a", epoch=1, generation=1,
) as guard:
    motion = (.03, 0.0)
    if stage in ("after_prepare", "inside_journal_sql", "after_journal",
                 "inside_registry_finalize"):
        expected_head, expected_tick = guard._prepare("step", motion)
    if stage == "inside_journal_sql":
        # Exactly one syntactically valid Noosphere INSERT, but without
        # COMMIT. SIGKILL should roll back this pending transaction.
        event = guard._plan("step", motion)
        db = guard._aq.store._db
        db.execute("BEGIN IMMEDIATE")
        previous = guard._registry._row(guard._organism_id)["head"]
        digest = _sha({"previous": previous, "event": event})
        assert digest == expected_head
        db.execute(
            """INSERT INTO events(event_id,agent_id,recorded_at_ns,
               observed_at_ns,valid_from_ns,valid_to_ns,topic,visibility,
               wire_json,prev_hash,event_hash)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (event["event_id"], event["agent_id"], event["recorded_at_ns"],
             event["observed_at_ns"], event["valid_from_ns"],
             event["valid_to_ns"], event["topic"], event["visibility"],
             _canonical(event), previous, digest),
        )
    if stage in ("after_journal", "inside_registry_finalize"):
        guard._aq.step(dx=motion[0], dy=motion[1])
        assert guard._aq.store.head() == expected_head
    if stage == "inside_registry_finalize":
        # Reproduce a transaction interrupted after BOTH SQL statements
        # have run but before COMMIT; SQLite must roll both back.
        registry_db = guard._registry._db
        registry_db.execute("BEGIN IMMEDIATE")
        registry_db.execute(
            "UPDATE authority SET phase='ACTIVE', head=?, tick=?, "
            "event_count=event_count+1, generation=generation+1 "
            "WHERE organism_id=?", (expected_head, expected_tick,
                                   guard._organism_id),
        )
        registry_db.execute(
            "DELETE FROM write_intents WHERE organism_id=?",
            (guard._organism_id,),
        )
    if stage == "after_finalize":
        guard.step(dx=motion[0], dy=motion[1])
    if stage not in (
        "after_prepare", "inside_journal_sql", "after_journal",
        "inside_registry_finalize", "after_finalize",
    ):
        raise ValueError("invalid isolated crash fixture stage")
    j = guard._aq.store._db
    r = guard._registry._db
    assert (j.execute("PRAGMA journal_mode").fetchone()[0].lower(),
            j.execute("PRAGMA synchronous").fetchone()[0]) == ("wal", 2)
    assert (r.execute("PRAGMA journal_mode").fetchone()[0].lower(),
            r.execute("PRAGMA synchronous").fetchone()[0]) == ("wal", 2)
    print("READY", stage, flush=True)
    signal.pause()
"""


@unittest.skipUnless(sys.platform.startswith("linux") and hasattr(signal, "SIGKILL"),
                     "requires Linux process SIGKILL and WAL semantics")
class SQLiteWalSigkillTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mojum-wal-kill-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.root.chmod(0o700)
        self.journal = self.root / "journal.sqlite3"
        self.registry = self.root / "authority.sqlite3"
        self.archive = self.root / "source-private.json"
        with Aquarium(self.journal, SEED, GRAPH) as q:
            q.step(dx=.01)
            manifest = export_quiescent_aquarium(q, self.archive)
        source = inspect_archive(
            self.archive, expected_organism_id=ORGANISM, expected_tick=1,
            expected_manifest_sha256=manifest["manifest_sha256"],
        )
        with LocalAuthority(self.registry, create=True) as r:
            r.provision(source, "node-a")
        install_intent_table(self.registry)
        self.workers = []
        self.addCleanup(self._cleanup_workers)

    def _cleanup_workers(self):
        for p in self.workers:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)
            if p.stdout:
                p.stdout.close()
            if p.stderr:
                p.stderr.close()

    def authority(self):
        with LocalAuthority(self.registry) as r:
            return r.state(ORGANISM)

    def inspect(self):
        return inspect_prepared(
            self.journal, self.registry, SEED, GRAPH,
            organism_id=ORGANISM,
        )

    def _kill_at(self, stage):
        command = [
            sys.executable, "-u", "-c", WORKER, stage,
            str(self.journal), str(self.registry),
            str(ROOT / "examples/genesis_seed.json"),
            str(ROOT / "examples/first_loop.json"),
        ]
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        p = subprocess.Popen(command, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env)
        self.workers.append(p)
        readable, _, _ = select.select([p.stdout], [], [], 8)
        if not readable:
            p.kill()
            p.wait(timeout=5)
            self.fail("SIGKILL worker never reached crash barrier; stderr=" +
                      p.stderr.read().decode("utf-8", "replace")[-1800:])
        line = p.stdout.readline().decode("utf-8", "replace").strip()
        if line != "READY " + stage:
            p.kill()
            p.wait(timeout=5)
            self.fail("bad worker barrier " + line + "; stderr=" +
                      p.stderr.read().decode("utf-8", "replace")[-1800:])
        # External SIGKILL: no Python finally handlers, SQLite close or
        # graceful WAL checkpoint from the killed owner process.
        p.kill()
        self.assertEqual(p.wait(timeout=5), -signal.SIGKILL)
        self.assertFalse(p.poll() is None)

    def _check_integrity_and_chain(self, *, count, tick):
        with closing(sqlite3.connect(self.journal)) as j:
            self.assertEqual(j.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(j.execute("SELECT count(*) FROM events").fetchone()[0],
                             count)
        with closing(sqlite3.connect(self.registry)) as r:
            self.assertEqual(r.execute("PRAGMA quick_check").fetchone()[0], "ok")
        with Aquarium(self.journal, SEED, GRAPH) as q:
            self.assertEqual(q.tick, tick)
            self.assertEqual(q.store.count(), count)
            self.assertEqual(q.store.verify_chain()["head"], q.store.head())

    def test_external_sigkill_after_prepare_preserves_frozen_intent(self):
        self._kill_at("after_prepare")
        result = self.inspect()
        self.assertEqual(result["classification"], "PREPARED_NO_EVENT_STOP")
        self.assertEqual(self.authority()["phase"], "FROZEN")
        self.assertEqual(self.authority()["event_count"], 2)
        self.assertFalse(result["auto_repair_allowed"])
        self._check_integrity_and_chain(count=2, tick=1)

    def test_external_sigkill_inside_uncommitted_noosphere_insert_rolls_back(self):
        self._kill_at("inside_journal_sql")
        result = self.inspect()
        self.assertEqual(result["classification"], "PREPARED_NO_EVENT_STOP")
        self.assertEqual(self.authority()["phase"], "FROZEN")
        self.assertEqual(self.authority()["event_count"], 2)
        self._check_integrity_and_chain(count=2, tick=1)

    def test_external_sigkill_after_journal_commit_keeps_exact_pending_intent(self):
        self._kill_at("after_journal")
        result = self.inspect()
        self.assertEqual(result["classification"],
                         "PREPARED_EXACT_EVENT_REQUIRES_REVIEW")
        self.assertEqual(result["intent"]["expected_head"],
                         result["journal"]["head"])
        self.assertEqual(self.authority()["phase"], "FROZEN")
        self.assertEqual(self.authority()["event_count"], 2)
        self.assertFalse(result["auto_repair_allowed"])
        self._check_integrity_and_chain(count=3, tick=2)

    def test_external_sigkill_inside_uncommitted_registry_finalization_rolls_back(self):
        self._kill_at("inside_registry_finalize")
        result = self.inspect()
        self.assertEqual(result["classification"],
                         "PREPARED_EXACT_EVENT_REQUIRES_REVIEW")
        authority = self.authority()
        self.assertEqual(authority["phase"], "FROZEN")
        self.assertEqual(authority["generation"], 1)
        self.assertEqual(authority["event_count"], 2)
        self._check_integrity_and_chain(count=3, tick=2)

    def test_external_sigkill_after_finalization_allows_verified_new_generation(self):
        self._kill_at("after_finalize")
        result = self.inspect()
        self.assertEqual(result["classification"], "NO_PREPARED_INTENT")
        state = self.authority()
        self.assertEqual(state["phase"], "ACTIVE")
        self.assertEqual(state["event_count"], 3)
        self.assertEqual(state["tick"], 2)
        self.assertEqual(state["generation"], 2)
        self._check_integrity_and_chain(count=3, tick=2)
        # A client that missed the ACK may not repeat an old generation.
        with self.assertRaises(AuthorityError):
            IntentGuardedAquarium(
                self.journal, self.registry, SEED, GRAPH,
                writer="node-a", epoch=1, generation=1,
            )
        with IntentGuardedAquarium(
            self.journal, self.registry, SEED, GRAPH,
            writer="node-a", epoch=1, generation=2,
        ) as q:
            self.assertEqual(q.checkpoint()["tick"], 2)

    def test_live_registry_connection_downgrade_fails_before_any_prepare(self):
        with IntentGuardedAquarium(
            self.journal, self.registry, SEED, GRAPH,
            writer="node-a", epoch=1, generation=1,
        ) as q:
            q._registry._db.execute("PRAGMA synchronous=NORMAL")
            with self.assertRaisesRegex(AuthorityError, "synchronous=FULL"):
                q.step(dx=.04)
            self.assertEqual(self.authority()["phase"], "ACTIVE")
            q._registry._db.execute("PRAGMA synchronous=FULL")
            q._aq.store._db.execute("PRAGMA synchronous=OFF")
            with self.assertRaisesRegex(AuthorityError, "synchronous=FULL"):
                q.step(dx=.04)
            q._aq.store._db.execute("PRAGMA synchronous=FULL")
            q.step(dx=.04)
            self.assertEqual(q.generation, 2)
        self.assertEqual(self.inspect()["classification"], "NO_PREPARED_INTENT")

    def test_post_append_durability_downgrade_keeps_intent_frozen(self):
        with IntentGuardedAquarium(
            self.journal, self.registry, SEED, GRAPH,
            writer="node-a", epoch=1, generation=1,
        ) as q:
            original_step = q._aq.step

            def downgrade(*args, **kwargs):
                event = original_step(*args, **kwargs)
                # Explicitly simulate trusted owner misconfiguration
                # between the two SQLite commits.
                q._registry._db.execute("PRAGMA synchronous=NORMAL")
                return event

            with patch.object(q._aq, "step", side_effect=downgrade):
                with self.assertRaisesRegex(AuthorityError, "synchronous=FULL"):
                    q.step(dx=.025)
        report = self.inspect()
        self.assertEqual(report["classification"],
                         "PREPARED_EXACT_EVENT_REQUIRES_REVIEW")
        self.assertEqual(self.authority()["phase"], "FROZEN")
        self._check_integrity_and_chain(count=3, tick=2)

    def test_wal_backup_sees_commits_that_plain_db_copy_cannot(self):
        # A long-lived reader prevents checkpointing newer WAL frames to
        # the primary file; a raw .sqlite3 copy is *not* a valid snapshot.
        with closing(sqlite3.connect(self.journal, isolation_level=None)) as reader:
            reader.execute("PRAGMA journal_mode=WAL")
            reader.execute("BEGIN")
            self.assertEqual(
                reader.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2,
            )
            with Aquarium(self.journal, SEED, GRAPH) as writer:
                writer.store._db.execute("PRAGMA wal_autocheckpoint=0")
                writer.step(dx=.037)
                committed_head = writer.store.head()
            self.assertTrue((self.root / "journal.sqlite3-wal").exists())
            raw_path = self.root / "unsafe-main-file-only.sqlite3"
            backed_path = self.root / "safe-sqlite-backup.sqlite3"
            shutil.copy2(self.journal, raw_path)
            with closing(sqlite3.connect(
                "file:" + str(self.journal) + "?mode=ro", uri=True,
            )) as source:
                with closing(sqlite3.connect(backed_path)) as backed:
                    source.backup(backed)
            with closing(sqlite3.connect(backed_path)) as backed:
                self.assertEqual(
                    backed.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3,
                )
                actual_head = backed.execute(
                    "SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1"
                ).fetchone()[0]
                self.assertEqual(actual_head, committed_head)
            with closing(sqlite3.connect(raw_path)) as unsafe:
                raw_count = unsafe.execute(
                    "SELECT COUNT(*) FROM events",
                ).fetchone()[0]
                self.assertEqual(raw_count, 2)
            reader.execute("ROLLBACK")
        # Source was legitimately committed out of band; it must NOT be
        # silently admitted as the registered writer's latest state.
        audit = self.inspect()
        self.assertEqual(audit["classification"],
                         "UNANCHORED_JOURNAL_WITHOUT_INTENT_STOP")
        self.assertFalse(audit["auto_repair_allowed"])


if __name__ == "__main__":
    unittest.main()
