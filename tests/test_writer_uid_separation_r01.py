"""Linux service/client UID separation: real socket permission and denied peers.

CI does not create a second OS account. It proves configured policy, kernel
SO_PEERCRED denial of the service UID, and private data-file gates. An end-to-
end enrolled different-UID connection needs privileged host integration QA.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from mojum.aquarium import Aquarium
from mojum.cold_aquarium import export_quiescent_aquarium
from mojum.journal_lineage import inspect_archive
from mojum.local_authority import LocalAuthority
from mojum.writer_service import WriterService, request

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text("utf-8"))
ID = SEED["genome_id"]


@unittest.skipUnless(os.name == "posix" and os.getuid() != 0
                     and hasattr(socket, "SO_PEERCRED"),
                     "Linux unprivileged owner + SO_PEERCRED required")
class WriterUidSeparationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.root.chmod(0o700)
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        self.ipc = self.root / "ipc"
        self.ipc.mkdir(mode=0o710)
        self.ipc.chmod(0o710)
        self.archive_dir = self.private / "archives"
        self.archive_dir.mkdir(mode=0o700)
        self.registry = self.private / "registry.sqlite3"
        self.db = self.private / "dante.sqlite3"
        self.base = self.private / "bootstrap.json"
        self.sock = self.ipc / "writer.sock"
        self.client_uid = os.getuid() + 10000
        self.client_gid = os.getgid()
        with Aquarium(self.db, SEED, GRAPH) as aquarium:
            aquarium.step(dx=0.01)
            exported = export_quiescent_aquarium(aquarium, self.base)
        evidence = inspect_archive(
            self.base, expected_organism_id=ID, expected_tick=1,
            expected_manifest_sha256=exported["manifest_sha256"],
        )
        with LocalAuthority(self.registry, create=True) as control:
            control.provision(evidence, "node-a")
        # Strict private mode is mandatory before cross-UID service startup.
        self.db.chmod(0o600)
        self.registry.chmod(0o600)
        self.proc = None
        self.addCleanup(self.stop)

    def new(self, **changes):
        opts = dict(
            writer="node-a", epoch=1, generation=1,
            archive_dir=self.archive_dir,
            client_uid=self.client_uid, client_gid=self.client_gid,
        )
        opts.update(changes)
        return WriterService(
            self.sock, self.db, self.registry, SEED, GRAPH, **opts,
        )

    def stop(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=4)
        if self.proc.stdout:
            self.proc.stdout.close()
        if self.proc.stderr:
            self.proc.stderr.close()

    def test_enrolled_uid_must_be_distinct_and_nonroot(self):
        for kw in (
            {"client_uid": os.getuid()},
            {"client_uid": 0},
            {"client_uid": -1},
            {"client_uid": True},
            {"client_uid": 2**31},
            {"client_uid": None},
            {"client_gid": None},
            {"client_gid": 0},
            {"client_gid": -1},
            {"client_gid": True},
            {"client_gid": 2**31},
        ):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.new(**kw)
            self.assertFalse(self.sock.exists())

    def test_service_must_be_member_of_requested_group(self):
        unknown = next(
            (gid for gid in (2**30 - 1, 2**30 - 2, 2**30 - 3)
             if gid not in (os.getgid(), *os.getgroups())),
        )
        with self.assertRaisesRegex(ValueError, "belong"):
            self.new(client_gid=unknown)
        self.assertFalse(self.sock.exists())

    def test_socket_group_access_and_private_journal_data(self):
        with self.new() as server:
            directory = self.ipc.stat()
            sock = self.sock.stat()
            self.assertEqual(directory.st_mode & 0o777, 0o710)
            self.assertEqual(sock.st_mode & 0o777, 0o660)
            self.assertEqual(sock.st_uid, os.getuid())
            self.assertEqual(sock.st_gid, self.client_gid)
            self.assertTrue(stat.S_ISSOCK(sock.st_mode))
            for path in (self.db, self.registry):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(server._allowed_client_uid, self.client_uid)
        self.assertFalse(self.sock.exists())

    def test_socket_group_parent_must_be_exact_0710(self):
        for mode in (0o700, 0o711, 0o750, 0o770, 0o755):
            self.ipc.chmod(mode)
            with self.subTest(mode=oct(mode)), self.assertRaisesRegex(
                ValueError, "0710",
            ):
                self.new()
            self.assertFalse(self.sock.exists())
        self.ipc.chmod(0o710)

    def test_world_readable_or_group_readable_journal_never_served(self):
        for file in (self.db, self.registry):
            for mode in (0o644, 0o660, 0o640, 0o666):
                file.chmod(mode)
                with self.subTest(path=file.name, mode=oct(mode)):
                    with self.assertRaisesRegex(ValueError, "0600"):
                        self.new()
                    self.assertFalse(self.sock.exists())
            file.chmod(0o600)

    def test_private_data_parent_and_archive_are_enforced(self):
        for parent in (self.private, self.archive_dir):
            parent.chmod(0o750)
            with self.subTest(parent=parent), self.assertRaises(ValueError):
                self.new()
            parent.chmod(0o700)
        self.assertFalse(self.sock.exists())

    def test_symlinked_sqlite_parent_refused(self):
        alias = self.root / "db-link"
        try:
            alias.symlink_to(self.private, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(str(exc))
        with self.assertRaises(ValueError):
            WriterService(
                self.sock, alias / self.db.name, self.registry,
                SEED, GRAPH, writer="node-a", epoch=1, generation=1,
                archive_dir=self.archive_dir,
                client_uid=self.client_uid, client_gid=self.client_gid,
            )
        self.assertFalse(self.sock.exists())

    def test_owner_os_uid_cannot_send_even_status_in_cross_uid_mode(self):
        # The current OS UID can physically reach this group socket as its
        # owner, but is rejected after the Linux peer credentials are read.
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mojum.writer_service", "serve",
             "--socket", str(self.sock), "--db", str(self.db),
             "--registry", str(self.registry),
             "--archive-dir", str(self.archive_dir),
             "--writer", "node-a", "--epoch", "1", "--generation", "1",
             "--client-uid", str(self.client_uid),
             "--client-gid", str(self.client_gid),
             "--genome", str(ROOT / "examples/genesis_seed.json"),
             "--graph", str(ROOT / "examples/first_loop.json")],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self.sock.exists():
            if self.proc.poll() is not None:
                self.fail(self.proc.stderr.read().decode("utf-8", "replace")[:1500])
            time.sleep(.02)
        self.assertTrue(self.sock.exists(), "service did not create Unix socket")
        answer = request(self.sock, {"op": "status"}, timeout=1)
        self.assertEqual(answer, {"ok": False, "error": "PermissionError"})
        with LocalAuthority(self.registry) as control:
            self.assertEqual(control.state(ID)["event_count"], 2)

    def test_permission_regression_after_start_fails_closed(self):
        with self.new() as service:
            self.db.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "0600"):
                service.serve_once()
            self.db.chmod(0o600)
            self.registry.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "0600"):
                service.serve_once()
            self.registry.chmod(0o600)
            self.ipc.chmod(0o770)
            with self.assertRaisesRegex(ValueError, "0710"):
                service.serve_once()
            self.ipc.chmod(0o710)

    def test_missing_data_file_prevents_service_start(self):
        missing = self.private / "missing.sqlite3"
        with self.assertRaisesRegex(ValueError, "unavailable"):
            WriterService(
                self.sock, missing, self.registry, SEED, GRAPH,
                writer="node-a", epoch=1, generation=1,
                archive_dir=self.archive_dir,
                client_uid=self.client_uid, client_gid=self.client_gid,
            )
        self.assertFalse(missing.exists())
        self.assertFalse(self.sock.exists())


if __name__ == "__main__":
    unittest.main()
