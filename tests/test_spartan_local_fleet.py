"""Offline tests for local 300-identity / bounded physical model Spartan fleet."""
import json
from contextlib import closing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

# Other repo audits invoke tests from tests/ with PYTHONPATH=src.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import spartan_coordinator as sc
from tools import spartan_local_fleet as fleet


def fake_server(port, path, body=None, timeout=5):
    if path == "/health":
        return {"status": "ok"}
    if path == "/slots":
        n = {18182: 4, 18180: 1, 18181: 1}[port]
        return [{"id": i, "n_ctx": 2048, "is_processing": False}
                for i in range(n)]
    if path == "/v1/chat/completions":
        if body["model"] not in ("GPT20B", "QwenCoder7B"):
            raise AssertionError("unexpected model")
        return {"choices": [{"message": {"content": "Proposed small diff and test plan."}}],
                "usage": {"completion_tokens": 9}}
    raise AssertionError(path)


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "db.sqlite3"
        self.out = self.root / "artifacts"
        self.out.mkdir(mode=0o700)
        with closing(sc.connect(str(self.db))) as db:
            sc.create_schema(db)
            sc.init(db, 300, 100)
        self.backends = fleet.discover(request=fake_server)

    def add_task(self, task_id, scope="module.one", *, clones=1):
        payload = {"prompt": "Implement a narrowly scoped test for one function.",
                   "role": "coder", "max_tokens": 64, "clones": clones}
        with closing(sc.connect(str(self.db))) as db:
            sc.enqueue(db, [{"id": task_id, "scope": scope,
                             "priority": 10, "payload": payload}], 101)

    def test_memory_database_does_not_create_literal_memory_file(self):
        # Guard against pre-creating a disk file named ':memory:' before
        # sqlite3 connects to its special in-memory database.
        repo_root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(repo_root) + (
            os.pathsep + environment["PYTHONPATH"]
            if environment.get("PYTHONPATH") else ""
        )
        script = (
            "from tools import spartan_coordinator as sc; "
            "db=sc.connect(':memory:'); "
            "sc.create_schema(db); "
            "sc.init(db, 1, 0); "
            "assert sc.status(db)['slots'] == 1; db.close()"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=self.root,
            env=environment, capture_output=True, text=True,
            timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / ":memory:").exists())

    def test_memory_sqlite_does_not_create_a_literal_database_file(self):
        # sqlite3 treats :memory: specially; the security preflight must not
        # accidentally turn it into a real file before SQLite opens it.
        previous_cwd = os.getcwd()
        try:
            os.chdir(self.root)
            with closing(sc.connect(":memory:")) as db:
                sc.create_schema(db)
                self.assertEqual(sc.init(db, 2, 100)["slots"], 2)
                self.assertEqual(db.execute("PRAGMA database_list").fetchone()[2], "")
            for suffix in ("", "-wal", "-shm"):
                self.assertFalse((self.root / (":memory:" + suffix)).exists())
        finally:
            os.chdir(previous_cwd)

    def test_coordinator_creates_owner_only_sqlite_with_permissive_umask(self):
        # Task payloads contain model prompts. Protect DB and WAL/SHM too,
        # not only the generated proposal artifacts.
        fresh = self.root / "private-prompts.sqlite3"
        previous = os.umask(0o022)
        try:
            with closing(sc.connect(str(fresh))) as db:
                sc.create_schema(db)
                sc.init(db, 300, 100)
                sc.enqueue(db, [{"id": "private-task", "scope": "private.scope",
                                 "payload": {"prompt": "private example"}}], 101)
                for name in (str(fresh), str(fresh) + "-wal", str(fresh) + "-shm"):
                    with self.subTest(file=name):
                        self.assertTrue(Path(name).is_file())
                        self.assertEqual(os.stat(name).st_mode & 0o077, 0)
        finally:
            os.umask(previous)

    def test_coordinator_rejects_public_and_symlinked_sqlite_state(self):
        exposed = self.root / "readable.sqlite3"
        exposed.touch()
        exposed.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "owner-only"):
            sc.connect(str(exposed))
        self.assertEqual(exposed.stat().st_mode & 0o777, 0o644)

        safe = self.root / "safe.sqlite3"
        safe.touch(mode=0o600)
        linked = self.root / "alias.sqlite3"
        linked.symlink_to(safe)
        with self.assertRaisesRegex(ValueError, "owner-only"):
            sc.connect(str(linked))

        wal = self.root / "fresh.sqlite3-wal"
        wal.touch()
        wal.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "owner-only"):
            sc.connect(str(self.root / "fresh.sqlite3"))

    def test_coordinator_rejects_untrusted_parent_directory(self):
        writable = self.root / "group-writable"
        writable.mkdir()
        writable.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "parent"):
            sc.connect(str(writable / "tasks.sqlite3"))
        self.assertFalse((writable / "tasks.sqlite3").exists())

    @unittest.skipUnless(hasattr(os, "geteuid"), "POSIX owner check")
    def test_coordinator_refuses_directory_owned_by_another_account(self):
        # A directory with mode 0755 may still be replaceable by its OWNER.
        # Checking only group/world writable bits is not sufficient.
        from types import SimpleNamespace

        parent = SimpleNamespace(
            st_mode=os.stat(self.root).st_mode,
            st_uid=os.geteuid() + 1,
        )
        proposed = self.root / "untrusted.sqlite3"
        with patch.object(sc.os, "lstat", return_value=parent):
            with self.assertRaisesRegex(ValueError, "owner-controlled"):
                sc.connect(str(proposed))
        self.assertFalse(proposed.exists())

    def test_status_never_creates_missing_sqlite_and_reads_existing(self):
        from contextlib import redirect_stdout
        from io import StringIO

        missing = self.root / "missing.sqlite3"
        argv = ["fleet", "--db", str(missing), "--output-dir", str(self.out),
                "--status"]
        with patch.object(sys, "argv", argv), \
             patch.object(fleet, "discover", return_value=self.backends):
            with self.assertRaisesRegex(fleet.FleetError, "database missing"):
                fleet.main()
        self.assertFalse(missing.exists())
        self.assertFalse(Path(str(missing) + "-wal").exists())
        self.assertFalse(Path(str(missing) + "-shm").exists())

        argv[2] = str(self.db)
        capture = StringIO()
        with patch.object(sys, "argv", argv), \
             patch.object(fleet, "discover", return_value=self.backends), \
             redirect_stdout(capture):
            fleet.main()
        payload = json.loads(capture.getvalue())
        self.assertEqual(payload["coordinator"]["slots"], 300)
        self.assertEqual(payload["live_free_model_slots"], 6)

    def test_live_slots_and_cpu_gpu_inventory(self):
        self.assertEqual(len(self.backends), 3)
        self.assertEqual(sum(x.free for x in self.backends), 6)
        self.assertEqual(self.backends[0].model, "QwenCoder7B")

    def test_gpu_gpt_reasoning_is_explicitly_low(self):
        gpt = next(b for b in self.backends if b.model == "GPT20B")
        seen = []
        def capture(port, path, body=None, timeout=5):
            seen.append(body)
            return {"choices": [{"message": {"content": "One focused test patch."}}],
                    "usage": {"completion_tokens": 22}}
        result = fleet.call_model(
            gpt, "Propose one minimal regression test only.", "test",
            384, request=capture)
        self.assertEqual(result["model"], "GPT20B")
        self.assertEqual(seen[0]["reasoning_effort"], "low")
        self.assertLessEqual(seen[0]["max_tokens"], 384)

        seen.clear()
        fleet.call_model(self.backends[0], "Propose one bounded code change.",
                         "coder", 128, request=capture)
        self.assertNotIn("reasoning_effort", seen[0])

    def test_reject_slot_metadata_that_could_overcommit(self):
        def broken(port, path, body=None, timeout=5):
            if path == "/health":
                return {"status": "ok"}
            if path == "/slots":
                return [{"id": 0, "n_ctx": 2048, "is_processing": False},
                        {"id": 0, "n_ctx": 2048, "is_processing": False}]
            raise AssertionError
        self.assertEqual(fleet.discover(request=broken), [])

    def test_private_review_artifact_and_exact_lease_completion(self):
        self.add_task("issue-123")
        result = fleet.single(self.db, 1, self.backends[0], self.out,
                              request=fake_server)
        self.assertEqual(result["state"], "proposal_for_independent_review")
        output = Path(result["artifact"])
        self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
        obj = json.loads(output.read_text())
        self.assertFalse(obj["patch_applied"])
        self.assertFalse(obj["tests_run"])
        self.assertFalse(obj["reviewed"])
        self.assertEqual(obj["model_calls"][0]["model"], "QwenCoder7B")
        with closing(sc.connect(str(self.db))) as db:
            self.assertEqual(sc.status(db)["states"]["review"], 1)

    def test_independent_clone_prefers_different_model_family(self):
        self.add_task("issue-123", clones=2)
        result = fleet.single(self.db, 1, self.backends[0], self.out,
                              others=self.backends, request=fake_server)
        obj = json.loads(Path(result["artifact"]).read_text())
        self.assertEqual(len(obj["model_calls"]), 2)
        self.assertEqual({x["model"] for x in obj["model_calls"]},
                         {"QwenCoder7B", "GPT20B"})
        self.assertTrue(obj["diverse_models"])
        self.assertNotEqual(obj["model_calls"][0]["port"],
                            obj["model_calls"][1]["port"])

    def test_clone_invocations_are_truly_parallel_and_bounded(self):
        from threading import Barrier, BoundedSemaphore
        self.add_task("parallel-clone", clones=2)
        # A sequential implementation would time out at the barrier,
        # proving model replicas were not actually called concurrently.
        both_entered = Barrier(2, timeout=3)
        called_ports = []
        def simultaneous(port, path, body=None, timeout=5):
            self.assertEqual(path, "/v1/chat/completions")
            called_ports.append(port)
            both_entered.wait()
            return {"choices": [{"message": {"content": "Independent proposal."}}],
                    "usage": {"completion_tokens": 16}}
        locks = {b.port: BoundedSemaphore(1) for b in self.backends}
        result = fleet.single(self.db, 1, self.backends[0], self.out,
                              others=self.backends, locks=locks,
                              request=simultaneous)
        self.assertEqual(result["state"], "proposal_for_independent_review")
        self.assertEqual(result["model_calls_completed"], 2)
        self.assertEqual(len(set(called_ports)), 2)
        with closing(sc.connect(str(self.db))) as db:
            self.assertEqual(sc.status(db)["states"]["review"], 1)

    def test_clone_fails_closed_without_second_backend(self):
        self.add_task("issue-123", clones=2)
        result = fleet.single(self.db, 1, self.backends[0], self.out,
                              others=(self.backends[0],), request=fake_server)
        self.assertEqual(result["state"], "blocked")

    def test_busy_physical_slot_is_retryable_without_unbounded_wait(self):
        from threading import BoundedSemaphore

        self.add_task("occupied-model")
        lock = BoundedSemaphore(1)
        lock.acquire()
        try:
            # A busy backend must not strand a time-limited coordinator lease.
            with patch.object(fleet, "MODEL_SLOT_WAIT_SECONDS", 0):
                result = fleet.single(self.db, 1, self.backends[0], self.out,
                                      locks={self.backends[0].port: lock},
                                      request=fake_server)
        finally:
            lock.release()
        self.assertEqual(result["state"], "retry_queued")
        self.assertFalse(list(self.out.iterdir()))
        with closing(sc.connect(str(self.db))) as db:
            self.assertEqual(sc.status(db)["states"]["queued"], 1)

    def test_bad_task_never_requests_untrusted_model(self):
        with closing(sc.connect(str(self.db))) as db:
            sc.enqueue(db, [{"id": "bad", "scope": "a",
                             "payload": {"prompt": "tiny"}}], 101)
        called = []
        def no_call(*a, **kw):
            called.append(a)
            raise AssertionError("called model on invalid task")
        r = fleet.single(self.db, 1, self.backends[0], self.out, request=no_call)
        self.assertEqual(r["state"], "blocked")
        self.assertFalse(called)

    def test_local_http_allowlist_prevents_external_destinations(self):
        # 18183 is an allowlisted optional local backend. Never make a real
        # network request in this hermetic rejection test.
        for port in (18184, 22, True, -1):
            with self.assertRaises(fleet.FleetError):
                fleet._http(port, "/health")
        with self.assertRaises(fleet.FleetError):
            fleet._http(18180, "/admin")
        with self.assertRaises(fleet.FleetError):
            fleet._http(18180, "/v1/chat/completions")

    def test_run_caps_to_available_slots_and_does_not_generate_300_calls(self):
        for i in range(8):
            self.add_task(f"t-{i}", scope=f"component.{i}")
        with patch.object(fleet, "discover", return_value=self.backends), \
             patch.object(fleet, "_http", side_effect=fake_server):
            outcome = fleet.run(self.db, self.out, max_agents=300, max_jobs=32)
        self.assertEqual(outcome["addressable_agents"], 300)
        self.assertEqual(outcome["live_free_model_slots"], 6)
        # Qwen advertised 4 physical slots, but the real four-way request
        # batch timed out. A successful 1-slot CPU + two 1-slot GPUs is
        # safer than 4 simultaneous CPU model calls that deliver no patches.
        self.assertEqual(outcome["per_backend_admission"],
                         {18182: 1, 18180: 1, 18181: 1})
        self.assertEqual(outcome["scheduled_primary_workers"], 3)
        self.assertEqual(outcome["proposals"], 3)
        with closing(sc.connect(str(self.db))) as db:
            self.assertEqual(sc.status(db)["states"]["queued"], 5)

    def test_clone_inflight_metrics_count_two_calls_for_one_worker(self):
        from threading import Barrier
        self.add_task("meter-clone", clones=2)
        both = Barrier(2, timeout=4)

        def simultaneous(port, path, body=None, timeout=5):
            self.assertEqual(path, "/v1/chat/completions")
            both.wait()
            return {"choices": [{"message": {"content": "proposal"}}],
                    "usage": {"completion_tokens": 5}}

        with patch.object(fleet, "discover", return_value=self.backends), \
             patch.object(fleet, "_http", side_effect=simultaneous):
            result = fleet.run(self.db, self.out, max_agents=300, max_jobs=1)
        self.assertEqual(result["scheduled_primary_workers"], 1)
        self.assertEqual(result["configured_physical_call_limit"], 3)
        self.assertEqual(result["model_invocation_attempts"], 2)
        self.assertEqual(result["observed_peak_inflight_model_calls"], 2)
        self.assertEqual(result["model_calls_completed"], 2)
        self.assertEqual(result["proposals"], 1)

    def test_cpu_admission_can_be_raised_only_explicitly(self):
        for i in range(10):
            self.add_task(f"case-{i}", scope=f"component.case.{i}")
        with patch.object(fleet, "discover", return_value=self.backends), \
             patch.object(fleet, "_http", side_effect=fake_server):
            result = fleet.run(self.db, self.out, max_agents=300, max_jobs=32,
                               max_cpu_inflight=2)
        self.assertEqual(result["per_backend_admission"][18182], 2)
        self.assertEqual(result["scheduled_primary_workers"], 4)
        self.assertEqual(result["proposals"], 4)

    def test_invalid_cpu_admission_rejected_before_running(self):
        for cap in (0, -1, True, 5, 300):
            with self.subTest(cap=cap), self.assertRaises(fleet.FleetError):
                fleet.run(self.db, self.out, max_agents=300, max_jobs=3,
                          max_cpu_inflight=cap, backends=self.backends)

    def test_interleaved_backend_admission_uses_gpu_immediately(self):
        for i in range(4):
            self.add_task(f"mixed-{i}", scope=f"component.mixed.{i}")
        with patch.object(fleet, "discover", return_value=self.backends), \
             patch.object(fleet, "_http", side_effect=fake_server):
            result = fleet.run(self.db, self.out, max_agents=300, max_jobs=2)
        self.assertEqual(result["proposals"], 2)
        outputs = list(self.out.glob("*.json"))
        self.assertEqual(len(outputs), 2)
        used_ports = {json.loads(p.read_text())["model_calls"][0]["port"]
                      for p in outputs}
        self.assertEqual(used_ports, {18182, 18180})

    def test_transient_model_outage_requeues_until_attempt_budget(self):
        self.add_task("transient-model")
        def unavailable(*args, **kwargs):
            raise fleet.TransientFleetError("synthetic endpoint outage")
        for expected in ("retry_queued", "retry_queued", "blocked"):
            result = fleet.single(self.db, 1, self.backends[0], self.out,
                                  request=unavailable)
            self.assertEqual(result["state"], expected)
        with closing(sc.connect(str(self.db))) as db:
            status = sc.status(db)
            self.assertEqual(status["states"]["blocked"], 1)
            self.assertEqual(status["states"]["queued"], 0)

    def test_artifact_write_failure_is_retryable_not_permanently_blocked(self):
        self.add_task("transient-artifact")
        with patch.object(fleet, "save_private",
                          side_effect=OSError("synthetic disk full")):
            outcome = fleet.single(self.db, 1, self.backends[0], self.out,
                                   request=fake_server)
        self.assertEqual(outcome["state"], "retry_queued")
        with closing(sc.connect(str(self.db))) as db:
            self.assertEqual(sc.status(db)["states"]["queued"], 1)
        # The same task can be reclaimed, fenced to a new attempt, and
        # reach review once its output store is available.
        accepted = fleet.single(self.db, 1, self.backends[0], self.out,
                                request=fake_server)
        self.assertEqual(accepted["state"], "proposal_for_independent_review")

    def test_durable_worker_identity_rotation_across_batches(self):
        # Six physical slots must not pin all future tasks to agents 1..6.
        for i in range(4):
            self.add_task(f"round-{i}", scope=f"component.round.{i}")
        with patch.object(fleet, "discover", return_value=self.backends), \
             patch.object(fleet, "_http", side_effect=fake_server):
            first = fleet.run(self.db, self.out, max_agents=300, max_jobs=2)
            second = fleet.run(self.db, self.out, max_agents=300, max_jobs=2)
        self.assertEqual({r["worker"] for r in first["results"]}, {1, 2})
        self.assertEqual({r["worker"] for r in second["results"]}, {3, 4})
        self.assertEqual(first["scheduled_primary_workers"], 2)
        self.assertEqual(second["scheduled_primary_workers"], 2)
        with closing(sc.connect(str(self.db))) as db:
            self.assertEqual(sc.status(db)["states"]["review"], 4)

    def test_no_duplicate_artifact_overwrite(self):
        x = fleet.save_private(self.out, "task-one", 1, {"a": 1})
        with self.assertRaises(FileExistsError):
            fleet.save_private(self.out, "task-one", 1, {"a": 2})
        self.assertEqual(json.loads(Path(x).read_text()), {"a": 1})

    @unittest.skipUnless(getattr(os, "O_NOFOLLOW", 0),
                         "platform cannot refuse lock symlinks")
    def test_fleet_lock_refuses_symlink_before_model_calls(self):
        target = self.root / "unrelated-private-file"
        target.write_bytes(b"unchanged")
        target.chmod(0o600)
        lock = Path(str(self.db) + ".fleet.lock")
        lock.symlink_to(target)
        with self.assertRaisesRegex(fleet.FleetError, "fleet lock"):
            fleet.run(self.db, self.out, max_agents=10, max_jobs=2,
                      backends=self.backends)
        self.assertEqual(target.read_bytes(), b"unchanged")

    def test_fleet_lock_refuses_legacy_world_readable_mode(self):
        lock = Path(str(self.db) + ".fleet.lock")
        lock.touch(mode=0o600)
        lock.chmod(0o644)
        with self.assertRaisesRegex(fleet.FleetError, "owner-only"):
            fleet.run(self.db, self.out, max_agents=10, max_jobs=2,
                      backends=self.backends)
        self.assertEqual(lock.stat().st_mode & 0o777, 0o644)

    def test_fleet_refuses_unsafe_parent_before_lock_creation(self):
        unsafe = self.root / "shared-state"
        unsafe.mkdir()
        unsafe.chmod(0o777)
        db_path = unsafe / "state.sqlite3"
        with self.assertRaisesRegex(fleet.FleetError, "directory/database"):
            fleet.run(db_path, self.out, max_agents=10, max_jobs=2,
                      backends=self.backends)
        self.assertFalse(Path(str(db_path) + ".fleet.lock").exists())

    def test_fail_if_another_worker_owns_lock(self):
        import fcntl
        lock = Path(str(self.db) + ".fleet.lock")
        lock.touch(mode=0o600)
        with open(lock, "a+") as fp:
            fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(fleet.FleetError):
                fleet.run(self.db, self.out, max_agents=10, max_jobs=2,
                          backends=self.backends)

    def test_reject_out_of_range_fleet(self):
        for agents, jobs in ((0, 1), (301, 1), (1, 33), (1, 0)):
            with self.assertRaises(fleet.FleetError):
                fleet.run(self.db, self.out, max_agents=agents, max_jobs=jobs,
                          backends=self.backends)


if __name__ == "__main__":
    unittest.main()
