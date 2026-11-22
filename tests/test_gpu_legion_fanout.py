"""Offline contract tests for fanout. Never calls live GPUs or the network."""
from collections import Counter
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

# The independent SQLite resource audit imports tests by absolute test path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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

    def test_tasks_input_is_bounded_before_json_parsing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.json"
            sample = tasks(1)
            path.write_text(json.dumps(sample), encoding="utf-8")
            self.assertEqual(f.load_tasks_file(path), sample)

            path.write_bytes(b" " * (f.MAX_TASK_FILE_BYTES + 1))
            with self.assertRaisesRegex(f.FanoutError, "byte limit"):
                f.load_tasks_file(path)
            path.write_text('[{"id":"x","agent_id":1,"prompt":NaN}]',
                            encoding="utf-8")
            with self.assertRaisesRegex(f.FanoutError, "nonfinite"):
                f.load_tasks_file(path)
            path.write_bytes(b"\xff")
            with self.assertRaisesRegex(f.FanoutError, "invalid tasks JSON"):
                f.load_tasks_file(path)

    @unittest.skipUnless(hasattr(os, "mkfifo") and getattr(os, "O_NONBLOCK", 0),
                         "POSIX FIFO support required")
    def test_fifo_tasks_path_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.fifo"
            os.mkfifo(path)
            command = [sys.executable, "-c",
                       "from tools.gpu_legion_fanout import load_tasks_file; "
                       "import sys; load_tasks_file(sys.argv[1])", str(path)]
            run = subprocess.run(command,
                                 cwd=Path(__file__).resolve().parents[1],
                                 text=True, capture_output=True, check=False,
                                 timeout=5)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("tasks input must be a regular file", run.stderr)

    @unittest.skipUnless(getattr(os, "O_NOFOLLOW", 0),
                         "platform has no O_NOFOLLOW")
    def test_symlink_tasks_path_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.json"
            path.write_text(json.dumps(tasks(1)), encoding="utf-8")
            link = Path(folder) / "link.json"
            link.symlink_to(path)
            with self.assertRaisesRegex(f.FanoutError, "cannot read tasks file"):
                f.load_tasks_file(link)

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


    @unittest.skipUnless(os.name=="posix", "POSIX directory permission gate")
    def test_world_writable_output_parent_refused_before_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            shared=directory/"shared"
            shared.mkdir()
            shared.chmod(0o777)
            tasks_path=directory/"tasks.json"
            tasks_path.write_text(json.dumps(tasks(1)),encoding="utf-8")
            output=shared/"should-not-exist.json"
            with patch.object(f,"discover_backend",
                              side_effect=lambda port:f.Backend(port,1,1,2048)), \\
                 patch.object(f,"execute",side_effect=AssertionError("inference started")) as execute:
                with self.assertRaisesRegex(f.FanoutError,"owner-controlled"):
                    f.main(["--tasks",str(tasks_path),"--execute","--output",str(output)])
                execute.assert_not_called()
            self.assertFalse(output.exists())

    @unittest.skipUnless(os.name=="posix", "POSIX symlink ancestor gate")
    def test_symlink_output_directory_ancestor_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            real=directory/"real"
            private=real/"private"
            private.mkdir(parents=True,mode=0o700)
            alias=directory/"alias"
            alias.symlink_to(real,target_is_directory=True)
            output=alias/"private"/"should-not-exist.json"
            with self.assertRaisesRegex(f.FanoutError,"symlink"):
                f.exclusive_json_output(output,{"private":"text"})
            self.assertFalse((private/"should-not-exist.json").exists())

    @unittest.skipUnless(os.name=="posix", "POSIX sticky ancestor gate")
    def test_private_output_beneath_sticky_ancestor_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            shared=directory/"sticky"
            shared.mkdir()
            shared.chmod(0o1777)
            private=shared/"mine"
            private.mkdir(mode=0o700)
            output=private/"result.json"
            f.exclusive_json_output(output,{"private":"text"})
            self.assertEqual(json.loads(output.read_text()),{"private":"text"})
            self.assertEqual(stat.S_IMODE(output.stat().st_mode),0o600)

    def test_existing_output_refused_before_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            tasks_path=directory/"tasks.json"
            tasks_path.write_text(json.dumps(tasks(1)),encoding="utf-8")
            output=directory/"existing.json"
            output.write_text("KEEP",encoding="utf-8")
            with patch.object(f,"discover_backend",
                              side_effect=lambda port:f.Backend(port,1,1,2048)), \
                 patch.object(f,"execute",side_effect=AssertionError("inference started")) as execute:
                with self.assertRaises(FileExistsError):
                    f.main(["--tasks",str(tasks_path),"--execute","--output",str(output)])
                execute.assert_not_called()
            self.assertEqual(output.read_text(encoding="utf-8"),"KEEP")

    def test_failed_model_call_cleans_pre_reserved_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            tasks_path=directory/"tasks.json"
            tasks_path.write_text(json.dumps(tasks(1)),encoding="utf-8")
            output=directory/"failed.json"
            with patch.object(f,"discover_backend",
                              side_effect=lambda port:f.Backend(port,1,1,2048)), \
                 patch.object(f,"execute",side_effect=RuntimeError("synthetic model failure")):
                with self.assertRaisesRegex(RuntimeError,"synthetic model failure"):
                    f.main(["--tasks",str(tasks_path),"--execute","--output",str(output)])
            self.assertFalse(output.exists())

    def test_successful_model_call_writes_private_pre_reserved_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            tasks_path=directory/"tasks.json"
            tasks_path.write_text(json.dumps(tasks(1)),encoding="utf-8")
            output=directory/"result.json"
            with patch.object(f,"discover_backend",
                              side_effect=lambda port:f.Backend(port,1,1,2048)), \
                 patch.object(f,"execute",return_value=[{"task_id":"task-0","text":"proposal"}]):
                self.assertEqual(
                    f.main(["--tasks",str(tasks_path),"--execute","--output",str(output)]),0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["results"][0]["text"],
                             "proposal")
            if os.name=="posix":
                self.assertEqual(stat.S_IMODE(output.stat().st_mode),0o600)


    def test_one_failed_call_does_not_erase_completed_peers(self):
        backends=[f.Backend(18180,1,1,2048),
                  f.Backend(18181,1,1,2048)]
        work=f.plan(f.validate_tasks(tasks(2)),backends)

        def mixed(job):
            if job.task_id=="task-1":
                raise RuntimeError("SECRET RAW PROVIDER FAILURE")
            return {"task_id":job.task_id, "agent_id":job.agent_id,
                    "clone":job.clone, "port":job.target_port,
                    "completion_tokens":4, "text":"valuable proposal"}

        result=f.execute(work,backends,caller=mixed)
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]["text"],"valuable proposal")
        self.assertEqual(result[1]["failed"],True)
        self.assertEqual(result[1]["error_type"],"RuntimeError")
        self.assertNotIn("SECRET",json.dumps(result))
        self.assertNotIn("text",result[1])

    def test_cli_partial_batch_saves_private_report_with_failure_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            tasks_path=directory/"tasks.json"
            tasks_path.write_text(json.dumps(tasks(2)),encoding="utf-8")
            output=directory/"partial.json"
            actual_execute=f.execute

            def mixed(job):
                if job.task_id=="task-1":
                    raise RuntimeError("SECRET RAW PROVIDER FAILURE")
                return {"task_id":job.task_id, "agent_id":job.agent_id,
                        "clone":job.clone, "port":job.target_port,
                        "completion_tokens":2, "text":"keep useful work"}

            with patch.object(f,"discover_backend",
                              side_effect=lambda port:f.Backend(port,1,1,2048)), \
                 patch.object(f,"execute",
                              side_effect=lambda work,backends:
                                  actual_execute(work,backends,caller=mixed)):
                self.assertEqual(f.main(["--tasks",str(tasks_path),
                                         "--execute","--output",str(output)]),2)

            report=json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["inference_requests_completed"],1)
            self.assertEqual(report["inference_requests_failed"],1)
            self.assertTrue(report["partial_batch"])
            self.assertEqual(report["results"][0]["text"],"keep useful work")
            self.assertTrue(report["results"][1]["failed"])
            self.assertNotIn("SECRET",json.dumps(report))
            if os.name=="posix":
                self.assertEqual(stat.S_IMODE(output.stat().st_mode),0o600)


if __name__=="__main__":
    unittest.main()
