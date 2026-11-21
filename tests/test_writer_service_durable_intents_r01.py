"""Opt-in durable-intent writer daemon with real Unix socket and SIGKILL-like exits.

CI failure stages are injected only in a dedicated child test process, NEVER
as production RPC operations or flags. No automatic recovery is exercised.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from mojum.aquarium import Aquarium
from mojum.cold_aquarium import export_quiescent_aquarium
from mojum.genesis import ACQUISITION_SCHEMA
from mojum.journal_lineage import inspect_archive
from mojum.local_authority import LocalAuthority
from mojum.write_intent import install_intent_table, inspect_prepared
from mojum.writer_service import request

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text("utf-8"))
ID = SEED["genome_id"]

FAULT_CHILD = r"""
import json, os, sys
from mojum.writer_service import WriterService
seed = json.loads(open(sys.argv[5], encoding="utf-8").read())
graph = json.loads(open(sys.argv[6], encoding="utf-8").read())
stage = sys.argv[4]
with WriterService(
    sys.argv[1], sys.argv[2], sys.argv[3],
    seed, graph, writer="node-a", epoch=1, generation=1,
    archive_dir=sys.argv[7], durable_intents=True,
) as server:
    guard = server._guard
    if stage == "after_prepare_before_journal":
        original = guard._prepare
        def inject_prepare(*args, **kwargs):
            original(*args, **kwargs)
            os._exit(71)
        guard._prepare = inject_prepare
    elif stage == "after_journal_before_authority":
        def inject_finish(*args, **kwargs):
            os._exit(72)
        guard._finish = inject_finish
    elif stage == "after_authority_before_response":
        original = guard._finish
        def inject_finish(*args, **kwargs):
            original(*args, **kwargs)
            os._exit(73)
        guard._finish = inject_finish
    else:
        raise ValueError("unrecognized test-only crash stage")
    server.serve_forever()
"""


def acquired(tick):
    return {
        "schema": ACQUISITION_SCHEMA,
        "event_id": "durable-daemon-acquisition",
        "genome_id": ID, "gene": "heartbeat_bpm",
        "delta": 11, "valid_from_tick": tick,
        "observed_at_ns": 0, "recorded_at_ns": tick * 10_000_000,
        "source": "synthetic-integration", "evidence": "fixture",
    }


@unittest.skipUnless(os.name == "posix" and hasattr(socket, "SO_PEERCRED"),
                     "Linux AF_UNIX local writer required")
class DurableIntentWriterServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.root.chmod(0o700)
        self.db = self.root / "journal.sqlite3"
        self.registry = self.root / "authority.sqlite3"
        self.archive = self.root / "origin.json"
        self.exports = self.root / "exports"
        self.exports.mkdir(mode=0o700)
        self.socket_path = self.root / "writer.sock"
        with Aquarium(self.db, SEED, GRAPH) as q:
            q.step(dx=.01)
            manifest = export_quiescent_aquarium(q, self.archive)
        record = inspect_archive(
            self.archive, expected_organism_id=ID, expected_tick=1,
            expected_manifest_sha256=manifest["manifest_sha256"],
        )
        with LocalAuthority(self.registry, create=True) as registry:
            registry.provision(record, "node-a")
        self.processes = []
        self.addCleanup(self.stop)

    def stop(self):
        for p in self.processes:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=4)
            if p.stdout:
                p.stdout.close()
            if p.stderr:
                p.stderr.close()

    def _env(self):
        return {**os.environ, "PYTHONPATH": str(ROOT / "src")}

    def state(self):
        with LocalAuthority(self.registry) as reg:
            return reg.state(ID)

    def pending(self):
        return inspect_prepared(
            self.db, self.registry, SEED, GRAPH, organism_id=ID,
        )

    def _command(self, socket_path, *, durable=True, generation=1):
        args = [
            sys.executable, "-m", "mojum.writer_service", "serve",
            "--socket", str(socket_path),
            "--db", str(self.db), "--registry", str(self.registry),
            "--archive-dir", str(self.exports),
            "--writer", "node-a", "--epoch", "1",
            "--generation", str(generation),
            "--genome", str(ROOT / "examples/genesis_seed.json"),
            "--graph", str(ROOT / "examples/first_loop.json"),
        ]
        if durable:
            args.append("--durable-intents")
        return args

    def launch(self, *, socket_path=None, durable=True,
               generation=1, fault=None, expect_start=True):
        socket_path = socket_path or self.socket_path
        cmd = (
            [sys.executable, "-c", FAULT_CHILD, str(socket_path), str(self.db),
             str(self.registry), fault,
             str(ROOT / "examples/genesis_seed.json"),
             str(ROOT / "examples/first_loop.json"), str(self.exports)]
            if fault else self._command(
                socket_path, durable=durable, generation=generation,
            )
        )
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=self._env())
        self.processes.append(p)
        if not expect_start:
            p.wait(timeout=7)
            self.assertNotEqual(p.returncode, 0)
            self.assertFalse(socket_path.exists())
            return p
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if p.poll() is not None:
                self.fail("writer exited before readiness: " +
                          p.stderr.read().decode("utf-8", "replace")[-2500:])
            if socket_path.exists():
                try:
                    reply = request(socket_path, {"op": "status"}, timeout=.2)
                    if reply["ok"]:
                        return p
                except (ValueError, OSError):
                    pass
            time.sleep(.02)
        self.fail("durable writer did not become ready")

    def rpc(self, msg, *, socket_path=None):
        return request(socket_path or self.socket_path, msg, timeout=2)

    def step_msg(self, generation, dx=.02):
        return {"op": "step", "expected_generation": generation,
                "dx": dx, "dy": 0}

    def test_default_mode_does_not_implicitly_require_intent_schema(self):
        self.launch(durable=False)
        status = self.rpc({"op": "status"})
        self.assertTrue(status["ok"])
        self.assertEqual(status["result"]["write_protocol"],
                         "legacy_local_guard_v1")
        reply = self.rpc(self.step_msg(1))
        self.assertTrue(reply["ok"])
        self.assertEqual(self.state()["phase"], "ACTIVE")

    def test_missing_schema_denies_durable_start_before_socket_creation(self):
        self.launch(durable=True, expect_start=False)
        self.assertEqual(self.state()["phase"], "ACTIVE")
        self.assertEqual(self.state()["event_count"], 2)
        self.assertFalse(self.socket_path.exists())

    def test_explicit_mode_real_step_acquire_and_export(self):
        install_intent_table(self.registry)
        self.launch()
        status = self.rpc({"op": "status"})
        self.assertTrue(status["ok"])
        self.assertEqual(status["result"]["write_protocol"], "durable_intent_v1")
        reply = self.rpc(self.step_msg(1, .03))
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["result"]["tick"], 2)
        self.assertEqual(reply["result"]["generation"], 2)
        learned = self.rpc({
            "op": "acquire", "expected_generation": 2, "event": acquired(2),
        })
        self.assertTrue(learned["ok"], learned)
        self.assertEqual(learned["result"]["tick"], 2)
        self.assertEqual(learned["result"]["generation"], 3)
        export = self.rpc({
            "op": "export", "expected_generation": 3,
            "filename": "private-durable.json",
        })
        self.assertTrue(export["ok"], export)
        self.assertTrue((self.exports / "private-durable.json").exists())
        self.assertEqual(self.state()["event_count"], 4)
        self.assertEqual(self.state()["generation"], 3)
        self.assertEqual(self.state()["phase"], "ACTIVE")
        self.assertEqual(self.pending()["classification"], "NO_PREPARED_INTENT")

    def test_rejected_inputs_do_not_leave_prepared_or_block_valid_write(self):
        install_intent_table(self.registry)
        self.launch()
        for cmd in (
            self.step_msg(1, "bad"),
            self.step_msg(1, 1e309),
            {"op": "acquire", "expected_generation": 1,
             "event": acquired(0)},
            self.step_msg(99, .01),
        ):
            with self.subTest(cmd=cmd):
                reply = self.rpc(cmd)
                self.assertFalse(reply["ok"], reply)
                self.assertEqual(self.state()["phase"], "ACTIVE")
                self.assertEqual(self.state()["generation"], 1)
                self.assertEqual(self.pending()["classification"], "NO_PREPARED_INTENT")
        ok = self.rpc(self.step_msg(1, .01))
        self.assertTrue(ok["ok"], ok)

    def _run_crash(self, stage, rc):
        install_intent_table(self.registry)
        self.launch(fault=stage)
        try:
            self.rpc(self.step_msg(1, .03))
        except (ValueError, OSError):
            pass  # Socket abruptly closes on os._exit: expected.
        proc = self.processes[-1]
        self.assertEqual(proc.wait(timeout=7), rc)
        self.assertTrue(self.socket_path.exists(),
                        "crashed process cannot clean up stale socket")
        self.assertEqual(self.state()["writer"], "node-a")
        report = self.pending()
        self.assertFalse(report["auto_repair_allowed"])
        self.assertFalse(report["automatic_writer_promotion_allowed"])
        return report

    def test_exit_after_prepare_without_journal_requires_review(self):
        report = self._run_crash("after_prepare_before_journal", 71)
        self.assertEqual(report["classification"], "PREPARED_NO_EVENT_STOP")
        self.assertEqual(self.state()["phase"], "FROZEN")
        self.assertEqual(self.state()["event_count"], 2)
        self.launch(socket_path=self.root / "new.sock", expect_start=False)
        self.assertEqual(self.state()["event_count"], 2)

    def test_exit_after_journal_before_authority_captures_exact_event(self):
        report = self._run_crash("after_journal_before_authority", 72)
        self.assertEqual(report["classification"],
                         "PREPARED_EXACT_EVENT_REQUIRES_REVIEW")
        self.assertEqual(report["intent"]["expected_head"],
                         report["journal"]["head"])
        self.assertEqual(self.state()["phase"], "FROZEN")
        self.assertEqual(self.state()["event_count"], 2)
        self.assertEqual(report["journal"]["event_count"], 3)
        self.launch(socket_path=self.root / "new.sock", expect_start=False)

    def test_exit_after_finalized_authority_can_restart_at_new_generation(self):
        report = self._run_crash("after_authority_before_response", 73)
        self.assertEqual(report["classification"], "NO_PREPARED_INTENT")
        self.assertEqual(self.state()["phase"], "ACTIVE")
        self.assertEqual(self.state()["event_count"], 3)
        self.assertEqual(self.state()["generation"], 2)
        # The original stale socket remains untouched. A separately chosen
        # operator-provided socket name avoids accidental implicit takeover.
        other = self.root / "replacement.sock"
        self.launch(socket_path=other, generation=2)
        denied = self.rpc(self.step_msg(1), socket_path=other)
        self.assertFalse(denied["ok"])
        accepted = self.rpc(self.step_msg(2), socket_path=other)
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(self.state()["generation"], 3)


if __name__ == "__main__":
    unittest.main()
