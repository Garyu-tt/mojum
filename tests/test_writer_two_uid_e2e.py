"""Ephemeral Linux end-to-end OS isolation QA: *real distinct UIDs*.

Runs ONLY in dedicated sudo-enabled CI with MOJUM_TWO_UID_E2E=1 and named
temporary Linux accounts. Nothing is installed or changed on user hosts.
"""
from __future__ import annotations

import grp
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from mojum.writer_service import request


RUN = (sys.platform.startswith("linux")
       and hasattr(socket, "SO_PEERCRED")
       and getattr(os, "geteuid", lambda: -1)() == 0
       and os.environ.get("MOJUM_TWO_UID_E2E") == "1")
# CI clone may be below a runner-only home directory. The subprocesses
# import the same commit's staged public src/examples from accessible /tmp.
ROOT = Path(os.environ.get(
    "MOJUM_TWO_UID_SOURCE", str(Path(__file__).resolve().parent.parent),
)).resolve()
SEED = ROOT / "examples/genesis_seed.json"
GRAPH = ROOT / "examples/first_loop.json"
PYTHON = sys.executable
WRITER = "mojumqawriter"
CLIENT = "mojumqaclient"
STRANGER = "mojumqastranger"
GROUP = "mojumqaipc"


_BOOTSTRAP = """
import json, os, sys
from pathlib import Path
from mojum.aquarium import Aquarium
from mojum.cold_aquarium import export_quiescent_aquarium
from mojum.journal_lineage import inspect_archive
from mojum.local_authority import LocalAuthority

os.umask(0o077)
journal, registry, backup, genome_path, graph_path = map(Path, sys.argv[1:])
seed = json.loads(genome_path.read_text("utf-8"))
graph = json.loads(graph_path.read_text("utf-8"))
with Aquarium(journal, seed, graph) as organism:
    organism.step(dx=0.01)
    manifest = export_quiescent_aquarium(organism, backup)
evidence = inspect_archive(
    backup, expected_organism_id=seed["genome_id"], expected_tick=1,
    expected_manifest_sha256=manifest["manifest_sha256"],
)
with LocalAuthority(registry, create=True) as controller:
    controller.provision(evidence, "node-a")
print(json.dumps({"head": evidence.head, "manifest_sha256": manifest["manifest_sha256"]}))
"""


_CLIENT = """
import json, sys
from mojum.writer_service import request
socket_path = sys.argv[1]
message = json.loads(sys.stdin.read())
print(json.dumps(request(socket_path, message, timeout=2.5), sort_keys=True))
"""


_READ = """
import os, sys
from pathlib import Path
try:
    fd = os.open(sys.argv[1], os.O_RDONLY)
except PermissionError:
    print("DENIED_BY_KERNEL")
    sys.exit(0)
except OSError as exc:
    print("OTHER_ERROR:" + type(exc).__name__)
    sys.exit(3)
else:
    os.close(fd)
    print("UNEXPECTED_RAW_READ")
    sys.exit(4)
"""


_BYPASS = """
import json, sys
from pathlib import Path
from mojum.aquarium import Aquarium
db, seed_path, graph_path = map(Path, sys.argv[1:])
seed = json.loads(seed_path.read_text("utf-8"))
graph = json.loads(graph_path.read_text("utf-8"))
with Aquarium(db, seed, graph) as q:
    q.step(dx=-0.02)
print("RAW_OWNER_WRITE_AS_FIXTURE")
"""


@unittest.skipUnless(RUN, "requires disposable root CI with real Linux accounts")
class DifferentLinuxUIDWriterTests(unittest.TestCase):
    """Process identities are kernel-checked; Python mocks are not used."""

    def setUp(self):
        self.writer = pwd.getpwnam(WRITER)
        self.client = pwd.getpwnam(CLIENT)
        self.stranger = pwd.getpwnam(STRANGER)
        self.ipc = grp.getgrnam(GROUP)
        uids = (self.writer.pw_uid, self.client.pw_uid, self.stranger.pw_uid)
        self.assertEqual(len(set(uids)), 3)
        self.assertTrue(all(uid > 0 for uid in uids))
        self.assertNotIn(self.ipc.gr_gid, (self.writer.pw_gid, self.client.pw_gid))
        self.tmp = tempfile.TemporaryDirectory(prefix="mojum-two-uid-ci-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        # Root-owned 0711 prefix permits traversal without exposing listings;
        # the actual private data parent remains service-owned 0700.
        self.root.chmod(0o711)
        self.private = self.root / "private"
        self.private.mkdir()
        os.chown(self.private, self.writer.pw_uid, self.writer.pw_gid)
        self.private.chmod(0o700)
        self.archives = self.private / "archives"
        self.archives.mkdir()
        os.chown(self.archives, self.writer.pw_uid, self.writer.pw_gid)
        self.archives.chmod(0o700)
        self.ipc_dir = self.root / "ipc"
        self.ipc_dir.mkdir()
        os.chown(self.ipc_dir, self.writer.pw_uid, self.ipc.gr_gid)
        self.ipc_dir.chmod(0o710)
        self.db = self.private / "aquarium.sqlite3"
        self.registry = self.private / "authority.sqlite3"
        self.source = self.private / "origin.json"
        self.sock = self.ipc_dir / "writer.sock"
        self._service = None
        self.addCleanup(self._stop)
        bootstrap = self._process(
            self.writer, [PYTHON, "-c", _BOOTSTRAP,
                          str(self.db), str(self.registry), str(self.source),
                          str(SEED), str(GRAPH)], timeout=20,
        )
        self.assertEqual(bootstrap.returncode, 0, bootstrap.stderr)
        self.bootstrap = json.loads(bootstrap.stdout)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.registry.stat().st_mode & 0o777, 0o600)

    def _env(self):
        return {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": "/tmp",
        }

    def _identity(self, account):
        # These are actual kernel process UIDs/GIDs, not simulated identity
        # headers. The supplementary IPC group permits Unix socket access.
        return {
            "user": account.pw_uid,
            "group": account.pw_gid,
            "extra_groups": [self.ipc.gr_gid],
        }

    def _process(self, account, command, *, input=None, timeout=15):
        return subprocess.run(
            command, input=input, text=True, capture_output=True,
            timeout=timeout, env=self._env(), **self._identity(account),
        )

    def _popen(self, account, command):
        return subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=self._env(),
            **self._identity(account),
        )

    def _launch(self, *, generation=1, must_start=True):
        assert self._service is None
        cmd = [
            PYTHON, "-m", "mojum.writer_service", "serve",
            "--socket", str(self.sock), "--db", str(self.db),
            "--registry", str(self.registry), "--archive-dir", str(self.archives),
            "--genome", str(SEED), "--graph", str(GRAPH),
            "--writer", "node-a", "--epoch", "1",
            "--generation", str(generation),
            "--client-uid", str(self.client.pw_uid),
            "--client-gid", str(self.ipc.gr_gid),
        ]
        self._service = self._popen(self.writer, cmd)
        if not must_start:
            self._service.wait(timeout=5)
            output = self._service.stderr.read().decode("utf-8", "replace")
            return self._service.returncode, output
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if self._service.poll() is not None:
                err = self._service.stderr.read().decode("utf-8", "replace")
                self.fail("service exited before readiness: " + err[-2200:])
            if self.sock.exists():
                try:
                    status = self._rpc(self.client, {"op": "status"})
                    if status.get("ok") is True:
                        return status["result"]
                except (OSError, ValueError, AssertionError):
                    pass
            time.sleep(0.03)
        self.fail("cross-UID socket handshake did not become ready")

    def _stop(self):
        if self._service is None:
            return
        proc = self._service
        self._service = None
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()

    def _rpc(self, account, message):
        r = self._process(account, [PYTHON, "-c", _CLIENT, str(self.sock)],
                          input=json.dumps(message), timeout=7)
        self.assertEqual(r.returncode, 0, r.stderr[-2200:])
        return json.loads(r.stdout)

    def _raw_denied(self, account, path):
        result = self._process(
            account, [PYTHON, "-c", _READ, str(path)],
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "DENIED_BY_KERNEL")

    def _unlink_stale_socket_after_verified_exit(self):
        # A killed daemon must never silently unlink or reuse another socket.
        # Privileged cleanup is the test operator's explicit action after
        # verifying PID exit and the path's original owner and type.
        self.assertIsNotNone(self._service)
        self.assertIsNotNone(self._service.poll())
        st = self.sock.lstat()
        self.assertTrue(stat.S_ISSOCK(st.st_mode))
        self.assertEqual(st.st_uid, self.writer.pw_uid)
        self.assertEqual(st.st_gid, self.ipc.gr_gid)
        self.assertEqual(st.st_mode & 0o777, 0o660)
        self.sock.unlink()

    def test_true_second_uid_can_mutate_but_cannot_open_raw_sqlite(self):
        before = self._launch()
        self.assertEqual(before["generation"], 1)
        self.assertEqual(before["tick"], 1)
        accepted = self._rpc(
            self.client, {"op": "step", "expected_generation": 1,
                          "dx": 0.03, "dy": 0.0},
        )
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(accepted["result"]["tick"], 2)
        self.assertEqual(accepted["result"]["generation"], 2)
        self.assertNotEqual(accepted["result"]["journal_head"], before["journal_head"])
        for file in (self.db, self.registry, self.source):
            with self.subTest(file=file.name):
                self._raw_denied(self.client, file)
                self._raw_denied(self.stranger, file)
        self._raw_denied(self.client, self.archives)
        state = self._rpc(self.client, {"op": "status"})
        self.assertTrue(state["ok"], state)
        self.assertEqual(state["result"]["journal_head"],
                         accepted["result"]["journal_head"])
        self.assertNotIn("PRIVATE", json.dumps(state))

    def test_stranger_same_group_and_owner_uid_both_denied(self):
        self._launch()
        for user in (self.stranger, self.writer):
            with self.subTest(uid=user.pw_uid):
                reply = self._rpc(user, {"op": "status"})
                self.assertEqual(reply, {"ok": False, "error": "PermissionError"})
                reply = self._rpc(
                    user, {"op": "step", "expected_generation": 1,
                           "dx": 0.03, "dy": 0.0},
                )
                self.assertFalse(reply["ok"])
        accepted = self._rpc(
            self.client, {"op": "step", "expected_generation": 1,
                          "dx": 0.02, "dy": 0.0},
        )
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(accepted["result"]["tick"], 2)

    def test_private_archive_export_and_crash_restart(self):
        self._launch()
        archive = self._rpc(
            self.client, {"op": "export", "expected_generation": 1,
                          "filename": "private-001.json"},
        )
        self.assertTrue(archive["ok"], archive)
        export_file = self.archives / "private-001.json"
        self.assertEqual(export_file.stat().st_mode & 0o777, 0o600)
        self._raw_denied(self.client, export_file)
        self._raw_denied(self.stranger, export_file)
        advanced = self._rpc(
            self.client, {"op": "step", "expected_generation": 1,
                          "dx": 0.01, "dy": 0.0},
        )
        self.assertTrue(advanced["ok"], advanced)
        self.assertEqual(advanced["result"]["generation"], 2)
        os.kill(self._service.pid, signal.SIGKILL)
        self._service.wait(timeout=5)
        self.assertTrue(self.sock.exists(), "SIGKILL leaves a stale socket name")
        # New daemon MUST reject the pre-existing socket, not hijack it.
        self._stop()
        code, error = self._launch(generation=2, must_start=False)
        self.assertNotEqual(code, 0)
        self.assertIn("already exists", error)
        self._unlink_stale_socket_after_verified_exit()
        self._stop()
        resumed = self._launch(generation=2)
        self.assertEqual(resumed["generation"], 2)
        self.assertEqual(resumed["tick"], 2)
        stale = self._rpc(
            self.client, {"op": "step", "expected_generation": 1,
                          "dx": 0.01, "dy": 0.0},
        )
        self.assertFalse(stale["ok"])
        accepted = self._rpc(
            self.client, {"op": "step", "expected_generation": 2,
                          "dx": 0.01, "dy": 0.0},
        )
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(accepted["result"]["generation"], 3)

    def test_privileged_out_of_band_journal_write_fails_restart_closed(self):
        # Intentionally use *owner* UID directly to demonstrate the residual
        # threat if trusted service identity is compromised or bypassed.
        # The client UID cannot do this because of the tested file isolation.
        raw = self._process(
            self.writer, [PYTHON, "-c", _BYPASS,
                          str(self.db), str(SEED), str(GRAPH)],
        )
        self.assertEqual(raw.returncode, 0, raw.stderr)
        self.assertIn("RAW_OWNER_WRITE_AS_FIXTURE", raw.stdout)
        code, error = self._launch(must_start=False)
        self.assertNotEqual(code, 0)
        self.assertIn("differs from authority anchor", error)
        # No new admission, registry not silently advanced. Separate
        # crash-consistency reconciliation must be explicitly designed.
        check = self._process(self.writer, [
            PYTHON, "-c", """
import sys
from mojum.local_authority import LocalAuthority
with LocalAuthority(sys.argv[1]) as x:
    print(x.state("dante-fixture-001")["event_count"])
""", str(self.registry),
        ])
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(check.stdout.strip(), "2")


if __name__ == "__main__":
    unittest.main()
