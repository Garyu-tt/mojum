"""Linux Unix-socket writer service: real journal mutation, freeze and fail-closed RPC."""
import json
import os
from pathlib import Path
import socket
import stat
import struct
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
from mojum.writer_service import MAX_RPC_BYTES, WriterService, request

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text("utf-8"))
ID = SEED["genome_id"]
CLI = [sys.executable, "-m", "mojum.writer_service"]


def acquired(tick):
    return {
        "schema": ACQUISITION_SCHEMA, "event_id": "service-acquired",
        "genome_id": ID, "gene": "heartbeat_bpm", "delta": 12,
        "valid_from_tick": tick, "observed_at_ns": 0,
        "recorded_at_ns": tick * 10_000_000,
        "source": "synthetic-ipc-test", "evidence": "fixture-only",
    }


@unittest.skipUnless(os.name == "posix" and hasattr(socket, "SO_PEERCRED"),
                     "Linux Unix peer credential socket required")
class WriterServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.root.chmod(0o700)
        self.db = self.root / "aquarium.sqlite3"
        self.registry = self.root / "registry.sqlite3"
        self.base = self.root / "bootstrap.json"
        self.sock = self.root / "writer.sock"
        self.exports = self.root / "exports"
        self.exports.mkdir(mode=0o700)
        with Aquarium(self.db, SEED, GRAPH) as aquarium:
            aquarium.step(dx=.02)
            self.manifest = export_quiescent_aquarium(aquarium, self.base)
        evidence = inspect_archive(
            self.base, expected_organism_id=ID, expected_tick=1,
            expected_manifest_sha256=self.manifest["manifest_sha256"],
        )
        with LocalAuthority(self.registry, create=True) as authority:
            authority.provision(evidence, "node-a")
        self.processes = []
        self.addCleanup(self.stop_services)

    def launch(self, *, sock=None, epoch=1, generation=1, archive_dir=None):
        sock = sock or self.sock
        archive_dir = archive_dir or self.exports
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        proc = subprocess.Popen(
            [*CLI, "serve", "--socket", str(sock), "--db", str(self.db),
             "--registry", str(self.registry), "--archive-dir", str(archive_dir),
             "--writer", "node-a", "--epoch", str(epoch),
             "--generation", str(generation),
             "--genome", str(ROOT / "examples/genesis_seed.json"),
             "--graph", str(ROOT / "examples/first_loop.json")],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.processes.append(proc)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode("utf-8", "replace")
                self.fail("writer daemon failed to start: " + stderr[:3000])
            if sock.exists():
                try:
                    if request(sock, {"op": "status"}, timeout=.2).get("ok"):
                        return proc
                except (OSError, ValueError):
                    pass
            time.sleep(.025)
        self.fail("writer socket failed to become ready")

    def stop_services(self):
        for proc in self.processes:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=4)
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()

    def send(self, req, sock=None):
        return request(sock or self.sock, req)

    def status(self):
        result = self.send({"op": "status"})
        self.assertTrue(result["ok"], result)
        return result["result"]

    def state(self):
        with LocalAuthority(self.registry) as auth:
            return auth.state(ID)

    def test_status_and_real_step_update_one_journal_event(self):
        self.launch()
        original = self.status()
        self.assertEqual(original["tick"], 1)
        self.assertEqual(original["generation"], 1)
        self.assertNotIn("neural_state", original)
        newer = self.send({"op": "step", "expected_generation": 1,
                           "dx": .03, "dy": 0})
        self.assertTrue(newer["ok"], newer)
        self.assertEqual(newer["result"]["tick"], 2)
        self.assertEqual(newer["result"]["generation"], 2)
        self.assertEqual(self.state()["event_count"], 3)
        self.assertEqual(self.state()["head"], newer["result"]["journal_head"])
        # The service owns its SQLite journal; test-only independent replay.
        with Aquarium(self.db, SEED, GRAPH) as reopened:
            self.assertEqual(reopened.tick, 2)
            self.assertEqual(reopened.store.count(), 3)

    def test_acquired_gene_and_future_frames_use_same_canonical_history(self):
        self.launch()
        result = self.send({"op": "acquire", "expected_generation": 1,
                            "event": acquired(1)})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["tick"], 1)
        self.assertEqual(result["result"]["generation"], 2)
        result = self.send({"op": "step", "expected_generation": 2,
                            "dx": .01, "dy": .02})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["generation"], 3)
        self.assertEqual(self.state()["event_count"], 4)
        with Aquarium(self.db, SEED, GRAPH) as q:
            self.assertEqual(q.genome.phenotype(2)["parameters"]["heartbeat_bpm"], 72)
            self.assertEqual(q.checkpoint()["journal_head"], self.state()["head"])

    def test_stale_generation_is_rejected_without_a_journal_append(self):
        self.launch()
        self.assertTrue(self.send({"op": "step", "expected_generation": 1,
                                   "dx": .02, "dy": 0})["ok"])
        wrong = self.send({"op": "step", "expected_generation": 1,
                           "dx": .02, "dy": 0})
        self.assertFalse(wrong["ok"])
        self.assertEqual(self.state()["event_count"], 3)
        self.assertEqual(self.status()["generation"], 2)

    def test_freeze_blocks_running_writer_and_stays_denied_after_transfer(self):
        self.launch()
        evidence = inspect_archive(
            self.base, expected_organism_id=ID, expected_tick=1,
        )
        with LocalAuthority(self.registry) as auth:
            frozen = auth.freeze_transfer(
                evidence, evidence, source_writer="node-a",
                target_writer="node-b", epoch=1, generation=1,
            )
        self.assertEqual(frozen["phase"], "FROZEN")
        refused = self.send({"op": "step", "expected_generation": 1,
                             "dx": 0, "dy": 0})
        self.assertFalse(refused["ok"])
        self.assertEqual(self.state()["event_count"], 2)
        with LocalAuthority(self.registry) as auth:
            activated = auth.activate_transfer(
                evidence, target_writer="node-b",
                prior_epoch=1, frozen_generation=2,
            )
        self.assertEqual(activated["epoch"], 2)
        self.assertFalse(self.send({"op": "step", "expected_generation": 1,
                                    "dx": 0, "dy": 0})["ok"])

    def test_path_traversal_export_refused_but_private_archive_succeeds(self):
        self.launch()
        for name in ("../secret.json", ".hidden", "/tmp/x", "a/b", "bad\0name"):
            with self.subTest(name=name):
                response = self.send({"op": "export",
                                      "expected_generation": 1,
                                      "filename": name})
                self.assertFalse(response["ok"], response)
        result = self.send({"op": "export", "expected_generation": 1,
                            "filename": "safe-001.json"})
        self.assertTrue(result["ok"], result)
        dest = self.exports / "safe-001.json"
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result["result"]["generation"], 1)
        self.assertEqual(result["result"]["archive"], "safe-001.json")
        self.assertFalse(self.send({"op": "export", "expected_generation": 1,
                                    "filename": "safe-001.json"})["ok"])

    def test_no_tcp_listener_and_private_owned_socket(self):
        self.launch()
        info = self.sock.stat()
        self.assertTrue(stat.S_ISSOCK(info.st_mode))
        self.assertEqual(info.st_mode & 0o777, 0o600)
        self.assertEqual(info.st_uid, os.getuid())
        with self.assertRaises((OSError, ValueError)):
            request(self.root / "unbound.sock", {"op": "status"})
        self.assertNotIn("PRIVATE", json.dumps(self.status()))

    def test_existing_socket_name_never_removed_or_stolen(self):
        self.launch()
        with self.assertRaisesRegex(ValueError, "already exists"):
            WriterService(self.sock, self.db, self.registry, SEED, GRAPH,
                          writer="node-a", epoch=1, generation=1,
                          archive_dir=self.exports)
        self.assertTrue(self.status())

    def test_client_input_schema_unknown_opcode_invalid_types_do_not_append(self):
        self.launch()
        malformed = (
            {"op": "unknown"},
            {"op": "status", "extra": 1},
            {"op": "step", "expected_generation": 1, "dx": True, "dy": 0},
            {"op": "step", "expected_generation": 1, "dx": .01},
            {"op": "acquire", "expected_generation": 1, "event": []},
            {"op": "export", "expected_generation": "1", "filename": "x.json"},
        )
        for message in malformed:
            with self.subTest(message=message):
                answer = self.send(message)
                self.assertFalse(answer["ok"], answer)
        self.assertEqual(self.state()["event_count"], 2)
        self.assertEqual(self.status()["generation"], 1)

    def test_duplicate_json_keys_and_oversized_raw_frames_fail_closed(self):
        self.launch()
        packets = (
            b'{"op":"status","op":"step"}',
            b'{"op":"status","a":NaN}',
        )
        for packet in packets:
            with self.subTest(packet=packet):
                with socket.socket(socket.AF_UNIX) as c:
                    c.settimeout(3)
                    c.connect(str(self.sock))
                    c.sendall(struct.pack("!I", len(packet)) + packet)
                    prefix = c.recv(4)
                    self.assertEqual(len(prefix), 4)
                    length = struct.unpack("!I", prefix)[0]
                    answer = json.loads(c.recv(length))
                    self.assertFalse(answer["ok"])
        with socket.socket(socket.AF_UNIX) as c:
            c.settimeout(3)
            c.connect(str(self.sock))
            c.sendall(struct.pack("!I", MAX_RPC_BYTES + 1))
            prefix = c.recv(4)
            length = struct.unpack("!I", prefix)[0]
            self.assertFalse(json.loads(c.recv(length))["ok"])
        self.assertEqual(self.state()["event_count"], 2)

    def test_no_socket_if_private_parent_not_owned_and_private(self):
        public_dir = self.root / "public"
        public_dir.mkdir(mode=0o755)
        with self.assertRaisesRegex(ValueError, "0700"):
            WriterService(public_dir / "writer.sock", self.db,
                          self.registry, SEED, GRAPH, writer="node-a",
                          epoch=1, generation=1, archive_dir=self.exports)
        self.assertFalse((public_dir / "writer.sock").exists())

    def test_cli_client_from_separate_process(self):
        self.launch()
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        result = subprocess.run(
            [*CLI, "request", "--socket", str(self.sock)],
            input=json.dumps({"op": "status"}), env=env,
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"]["tick"], 1)
        rejected = subprocess.run(
            [*CLI, "request", "--socket", str(self.sock)],
            input=json.dumps({"op": "step", "expected_generation": 99,
                              "dx": 0, "dy": 0}),
            env=env, text=True, capture_output=True,
        )
        self.assertEqual(rejected.returncode, 3)
        self.assertEqual(self.state()["event_count"], 2)

    def test_two_services_same_journal_cannot_both_advance(self):
        self.launch()
        second = self.root / "writer2.sock"
        self.launch(sock=second)
        one = self.send({"op": "step", "expected_generation": 1,
                         "dx": .01, "dy": 0})
        self.assertTrue(one["ok"], one)
        two = self.send({"op": "step", "expected_generation": 1,
                         "dx": .01, "dy": 0}, sock=second)
        self.assertFalse(two["ok"])
        self.assertEqual(self.state()["event_count"], 3)

    def test_lost_registry_file_refuses_step_before_commit(self):
        self.launch()
        parked = self.root / "parked.sqlite3"
        os.rename(self.registry, parked)
        try:
            response = self.send({"op": "step", "expected_generation": 1,
                                  "dx": 0, "dy": 0})
            self.assertFalse(response["ok"])
        finally:
            os.rename(parked, self.registry)
        self.assertEqual(self.state()["event_count"], 2)


if __name__ == "__main__":
    unittest.main()
