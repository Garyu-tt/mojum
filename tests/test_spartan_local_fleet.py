"""Offline tests for local 300-identity / bounded physical model Spartan fleet."""
import json
from contextlib import closing
import os
from pathlib import Path
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

    def test_live_slots_and_cpu_gpu_inventory(self):
        self.assertEqual(len(self.backends), 3)
        self.assertEqual(sum(x.free for x in self.backends), 6)
        self.assertEqual(self.backends[0].model, "QwenCoder7B")

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

    def test_clone_fails_closed_without_second_backend(self):
        self.add_task("issue-123", clones=2)
        result = fleet.single(self.db, 1, self.backends[0], self.out,
                              others=(self.backends[0],), request=fake_server)
        self.assertEqual(result["state"], "blocked")

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
        for port in (18183, 22, True, -1):
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
        self.assertEqual(outcome["admitted_concurrent_model_calls"], 6)
        # No claims for extra nonexistent physical slots.
        self.assertEqual(outcome["proposals"], 6)
        with closing(sc.connect(str(self.db))) as db:
            self.assertEqual(sc.status(db)["states"]["queued"], 2)

    def test_no_duplicate_artifact_overwrite(self):
        x = fleet.save_private(self.out, "task-one", 1, {"a": 1})
        with self.assertRaises(FileExistsError):
            fleet.save_private(self.out, "task-one", 1, {"a": 2})
        self.assertEqual(json.loads(Path(x).read_text()), {"a": 1})

    def test_fail_if_another_worker_owns_lock(self):
        import fcntl
        lock = Path(str(self.db) + ".fleet.lock")
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
