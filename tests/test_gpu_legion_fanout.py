"""Offline contract tests for fanout. Never calls live GPUs or the network."""
from collections import Counter
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from tools import gpu_legion_fanout as f


def tasks(n=3,clones=1):
    return [{"id":f"task-{i}","agent_id":i+1,
             "prompt":f"Do bounded work {i}","clones":clones}
            for i in range(n)]


def read_only(port, path, **kwargs):
    if path=="/health":return {"status":"ok"}
    if path=="/slots":
        n={18180:4,18181:2}[port]
        return [{"id":i,"n_ctx":2048,"is_processing":False}
                for i in range(n)]
    raise AssertionError("unexpected network call")


class FanoutContract(unittest.TestCase):
    def test_discovers_exact_live_slots_not_300_fake_model_processes(self):
        b1=f.discover_backend(18180,request=read_only)
        b2=f.discover_backend(18181,request=read_only)
        self.assertEqual((b1.total_slots,b1.free_slots,b1.per_slot_context),
                         (4,4,2048))
        self.assertEqual((b2.total_slots,b2.free_slots),(2,2))

    def test_busy_slots_are_subtracted_from_admission(self):
        def mock_read(port,path,**kwargs):
            if path=="/health":return {"status":"ok"}
            return [{"id":0,"n_ctx":2048,"is_processing":True},
                    {"id":1,"n_ctx":2048,"is_processing":False}]
        b=f.discover_backend(18180,request=mock_read)
        self.assertEqual(b.free_slots,1)

    def test_invalid_slot_types_and_duplicates_fail_closed(self):
        slots=read_only(18180,"/slots")
        for changed in (slots[:-1]+[dict(slots[0])],
                        slots[:-1]+[{"id":"bad","n_ctx":2048,"is_processing":False}],
                        slots[:-1]+[{"id":9,"n_ctx":2048,"is_processing":1}],
                        slots[:-1]+[{"id":9,"n_ctx":0,"is_processing":False}]):
            with self.subTest(changed=changed),self.assertRaises(f.FanoutError):
                f.discover_backend(18180,request=lambda port,path:
                  {"status":"ok"} if path=="/health" else changed)

    def test_two_clones_always_go_to_two_distinct_physical_replicas(self):
        plan=f.plan(f.validate_tasks(tasks(3,2)),
                    [f.Backend(18180,4,4,2048),
                     f.Backend(18181,2,2,2048)])
        self.assertEqual(len(plan),6)
        for task in ("task-0","task-1","task-2"):
            self.assertEqual({w.target_port for w in plan if w.task_id==task},
                             {18180,18181})

    def test_no_silent_duplicate_if_one_gpu_replica_down(self):
        with self.assertRaises(f.FanoutError):
            f.plan(f.validate_tasks(tasks(1,2)),
                   [f.Backend(18180,1,1,1024)])
        with self.assertRaises(f.FanoutError):
            f.plan(f.validate_tasks(tasks(1,1)),
                   [f.Backend(18180,1,0,1024)])

    def test_weighted_fairness_and_budget(self):
        backends=[f.Backend(18180,4,4,2048),f.Backend(18181,2,2,2048)]
        jobs=f.plan(f.validate_tasks(tasks(6)),backends)
        self.assertEqual(dict(Counter(x.target_port for x in jobs)),
                         {18180:4,18181:2})
        with self.assertRaises(f.FanoutError):
            f.plan(f.validate_tasks(tasks(32,2)),backends,limit=32)
        self.assertEqual(len(f.plan(f.validate_tasks(tasks(300,2)),
                                    backends,limit=600)),600)

    def test_task_validation_rejects_untrusted_shapes(self):
        wrong=[{"id":"bad space","agent_id":1,"prompt":"x"},
               {"id":"x","agent_id":301,"prompt":"x"},
               {"id":"x","agent_id":True,"prompt":"x"},
               {"id":"x","agent_id":1,"prompt":"x","clones":3},
               {"id":"x","agent_id":1,"prompt":"x","max_tokens":300},
               {"id":"x","agent_id":1,"prompt":"x","system":"x"*4097},
               {"id":"x","agent_id":1,"prompt":"x"*8193}]
        for bad in wrong:
            with self.subTest(bad=bad),self.assertRaises(f.FanoutError):
                f.validate_tasks([bad])
        with self.assertRaises(f.FanoutError):
            f.validate_tasks(tasks(301))

    def test_loopback_allowlist_and_post_paths(self):
        for port,path in ((22,"/health"),(18080,"/health"),
                          (18180,"http://example.invalid"),
                          (18180,"/admin")):
            with self.assertRaises(f.FanoutError):
                f._request_json(port,path,opener=Mock())
        with self.assertRaises(f.FanoutError):
            f._request_json(18180,"/slots",body={"code":"bad"},opener=Mock())
        with self.assertRaises(f.FanoutError):
            f._NoRedirect().redirect_request(
                None,None,302,"no",{},"https://example.org")

    def test_infer_never_executes_generated_commands(self):
        observed=[]
        def fake(port,path,**kwargs):
            observed.append((port,path,kwargs))
            return {"choices":[{"message":{"content":"echo test"}}],
                    "usage":{"completion_tokens":3}}
        w=f.Work("one",1,0,18180,"role","test",32)
        got=f.infer(w,request=fake)
        self.assertEqual(got["text"],"echo test")
        self.assertEqual(got["completion_tokens"],3)
        self.assertEqual(observed[0][0],18180)
        self.assertIn("clone: 0",
                      observed[0][2]["body"]["messages"][0]["content"])

    def test_concurrency_never_exceeds_live_backend_capacity(self):
        lock=threading.Lock()
        current=Counter()
        peak=Counter()
        def fake(w):
            with lock:
                current[w.target_port]+=1
                peak[w.target_port]=max(peak[w.target_port],
                                        current[w.target_port])
            time.sleep(.009)
            with lock:current[w.target_port]-=1
            return {"task_id":w.task_id,"clone":w.clone,
                    "port":w.target_port,"text":"ok"}
        backends=[f.Backend(18180,2,2,2048),f.Backend(18181,1,1,2048)]
        work=f.plan(f.validate_tasks(tasks(12)),backends)
        results=f.execute(work,backends,caller=fake)
        self.assertEqual(len(results),12)
        self.assertLessEqual(peak[18180],2)
        self.assertLessEqual(peak[18181],1)
        self.assertEqual(len(set(x["task_id"] for x in results)),12)

    def test_exclusive_output_is_private_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"output.json"
            f.exclusive_json_output(path,{"results":[{"text":"secret"}]})
            self.assertEqual(json.loads(path.read_text())["results"][0]["text"],
                             "secret")
            if os.name=="posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            with self.assertRaises(FileExistsError):
                f.exclusive_json_output(path,{"overwritten":True})


if __name__=="__main__":
    unittest.main()
