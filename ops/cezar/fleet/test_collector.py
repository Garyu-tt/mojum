"""Hermetic Cezar Fleet observer tests: no invented running agents."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
SRC=HERE/"collector.py" if (HERE/"collector.py").exists() else Path("/var/lib/cezar/fleet-visibility-r01.py")
spec=importlib.util.spec_from_file_location("cezar_fleet_collector",SRC)
f=importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)


class FleetObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.private=self.home/"private"
        self.private.mkdir(mode=0o700)
        self.now=datetime(2026,11,22,20,59,tzinfo=timezone.utc)
        self.schedules=self.private/"automations.json"
        rows=[
            {"title":f"Кубик D3 mojum — peer {i}/5","enabled":True,
             "last_run":"2026-11-22T20:30:00+00:00",
             "next_run":"2026-11-22T21:30:00+00:00",
             "last_status":"completed",
             "last_completed":"2026-11-22T20:35:00+00:00",
             "id":"PRIVATE_AUTOMATION_ID"} for i in range(1,5)]
        rows.append({"title":"Mojum — независимая приёмка","enabled":True,
                     "last_run":"2026-11-22T20:40:00+00:00",
                     "next_run":"2026-11-22T21:40:00+00:00",
                     "last_status":"completed",
                     "last_completed":"2026-11-22T20:41:00+00:00",
                     "id":"ANOTHER_PRIVATE_ID"})
        blob={"schema":"cezar.external-automations.v1",
              "observed_at":"2026-11-22T20:55:00+00:00","agents":rows}
        self.schedules.write_text(json.dumps(blob),encoding="utf-8")
        self.schedules.chmod(0o600)
        self.patch_source=patch.object(f,"SCHEDULES",self.schedules)
        self.patch_source.start()
        self.addCleanup(self.patch_source.stop)

    def fake_get(self,url):
        if url.endswith("/runs"):
            return [{"status":"cancelled"},{"status":"failed"},{"status":"running"}]
        if "/github?" in url:
            return {"available":True,"repo":"Garyu-tt/mojum","prsTotal":45,
                    "issuesTotal":33,
                    "prs":[{"number":712,"title":"feat: <new> & safe",
                            "isDraft":True,"createdAt":"2026-11-22T20:50:00Z"},
                           {"number":700,"title":"old PR",
                            "isDraft":False,"createdAt":"2026-11-21T18:00:00Z"}],
                    "issues":[{"number":689,"title":"MuJoCo issue",
                               "createdAt":"2026-11-22T20:15:00Z"}]}
        if url.startswith("http://127.0.0.1:480") and url.endswith("/slots"):
            return [{"is_processing":True},{"is_processing":False}]
        raise AssertionError("unexpected URL "+url)

    def test_separates_schedules_from_live_cezar_and_model_sessions(self):
        with patch.object(f,"_get",side_effect=self.fake_get):
            snap=f.collect(now=self.now)
        self.assertEqual(snap["schedules"]["enabled"],5)
        self.assertEqual(snap["schedules"]["developers_enabled"],4)
        self.assertEqual(snap["schedules"]["qa_enabled"],1)
        self.assertEqual(snap["schedules"]["ran_last_90m"],5)
        self.assertTrue(snap["schedules"]["snapshot_fresh"])
        self.assertEqual(snap["schedules"]["agents"][0]["last_status"],"completed")
        self.assertIsNotNone(snap["schedules"]["agents"][0]["last_completed"])
        self.assertEqual(snap["cezar"]["active"],1)
        self.assertEqual(snap["models"]["capacity_slots_known"],10)
        self.assertEqual(snap["models"]["inference_busy_known"],5)
        self.assertEqual(snap["github"]["new_prs_last_90m_visible"],1)
        self.assertEqual(snap["github"]["open_prs"],45)
        self.assertIsNone(snap["external_running_confirmed"])
        self.assertNotIn("PRIVATE_AUTOMATION_ID",json.dumps(snap))

    def test_idle_slots_do_not_become_active_agents(self):
        def get(url):
            if "/slots" in url:
                return [{"is_processing":False}]
            return self.fake_get(url)
        with patch.object(f,"_get",side_effect=get):
            snap=f.collect(now=self.now)
        self.assertEqual(snap["models"]["inference_busy_known"],0)
        self.assertEqual(snap["schedules"]["enabled"],5)
        self.assertIsNone(snap["external_running_confirmed"])

    def test_unreachable_model_is_not_reported_as_idle(self):
        def get(url):
            if ":48083" in url: raise TimeoutError("offline")
            return self.fake_get(url)
        with patch.object(f,"_get",side_effect=get):
            snap=f.collect(now=self.now)
        self.assertFalse(snap["models"]["capacity_complete"])
        self.assertEqual(snap["models"]["online_endpoints"],4)
        self.assertEqual(snap["models"]["capacity_slots_known"],8)
        self.assertIsNone(next(a for a in snap["models"]["endpoints"] if a["port"]==48083)["busy"])

    def test_stale_automation_snapshot_is_not_presented_as_live(self):
        out=f._schedule_list(self.now+timedelta(hours=4))
        self.assertEqual(out["enabled"],5)
        self.assertFalse(out["snapshot_fresh"])
        self.assertIsNone(out["running"])
        self.assertFalse(out["agents"][0]["last_90m"])

    @unittest.skipUnless(hasattr(os, "mkfifo") and os.name == "posix",
                         "POSIX named pipes required")
    def test_fifo_schedule_snapshot_is_rejected_without_blocking(self):
        # A blocking read-only FIFO open would freeze the whole collector.
        # The subprocess timeout keeps this regression bounded.
        self.schedules.unlink()
        os.mkfifo(self.schedules, 0o600)
        code = (
            "import importlib.util,sys\n"
            "from pathlib import Path\n"
            "from datetime import datetime,timezone\n"
            "spec=importlib.util.spec_from_file_location('cezar_schedule_check',sys.argv[1])\n"
            "module=importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "module.SCHEDULES=Path(sys.argv[2])\n"
            "try:\n"
            "    module._schedule_list(datetime.now(timezone.utc))\n"
            "except module.FleetError:\n"
            "    sys.exit(0)\n"
            "sys.exit(3)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(SRC), str(self.schedules)],
            capture_output=True, text=True, timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_untrusted_schedule_symlink_fails_closed(self):
        link=self.home/"link.json"
        link.symlink_to(self.schedules)
        with patch.object(f,"SCHEDULES",link):
            with self.assertRaises(f.FleetError):
                f._schedule_list(self.now)

    def test_unknown_schedule_is_not_faked_as_zero(self):
        self.schedules.write_text('{"schema":"wrong","agents":[]}')
        with patch.object(f,"_get",side_effect=self.fake_get):
            snap=f.collect(now=self.now)
        self.assertFalse(snap["schedules"]["available"])
        self.assertNotIn("enabled",snap["schedules"])
        self.assertIsNone(snap["external_running_confirmed"])

    def test_non_object_schedule_payload_never_crashes_collector(self):
        # Well-formed JSON is not necessarily an object with expected keys.
        for payload in ("[]", "null", "123", '"string"'):
            with self.subTest(payload=payload):
                self.schedules.write_text(payload,encoding="utf-8")
                with patch.object(f,"_get",side_effect=self.fake_get):
                    snap=f.collect(now=self.now)
                self.assertFalse(snap["schedules"]["available"])
                self.assertNotIn("enabled",snap["schedules"])
                self.assertIsNone(snap["external_running_confirmed"])

    def test_schedule_snapshot_is_read_through_pinned_bounded_descriptor(self):
        # Avoid Path.read_bytes(): a size check after an unbounded read
        # does not protect against a rapidly growing or replaced file.
        with patch.object(Path,"read_bytes",
                          side_effect=AssertionError("unsafe unbounded read")):
            with patch.object(f,"_get",side_effect=self.fake_get):
                snap=f.collect(now=self.now)
        self.assertEqual(snap["schedules"]["enabled"],5)
        self.schedules.write_bytes(b" "*(32768+1))
        with patch.object(f,"_get",side_effect=self.fake_get):
            oversized=f.collect(now=self.now)
        self.assertFalse(oversized["schedules"]["available"])
        self.assertNotIn("enabled",oversized["schedules"])

    def test_atomic_private_write_and_no_arbitrary_paths(self):
        out=self.private/"current.json"
        with patch.object(f,"PRIVATE",out):
            snap={"schema":"cezar.fleet.visibility.v1","external_running_confirmed":None}
            f.save(snap,out)
            f.save(snap,out)
            self.assertEqual(out.stat().st_mode&0o777,0o600)
            self.assertEqual(json.loads(out.read_text()),snap)
            with self.assertRaises(f.FleetError):
                f.save(snap,self.home/"arbitrary.json")
        self.assertEqual(list(self.private.glob("*.tmp*")),[])

    def test_forbid_world_readable_output_directory(self):
        pub=self.home/"public";pub.mkdir(mode=0o755);pub.chmod(0o755)
        out=pub/"snapshot.json"
        with patch.object(f,"WEB",out):
            with self.assertRaises(f.FleetError):
                f.save({"schema":"test"},out)

    def test_reject_non_json_or_unexpected_cezar_response(self):
        with patch.object(f,"_get",side_effect=lambda url: {"broken":True}):
            snap=f.collect(now=self.now)
        self.assertFalse(snap["cezar"]["available"])
        self.assertFalse(snap["github"]["available"])
        self.assertIsNone(snap["external_running_confirmed"])


class GitHubLifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.parent = Path(temp.name) / "private"
        self.parent.mkdir(mode=0o700)
        self.cache = self.parent / "recent-closed.json"
        self.now = datetime(2026, 11, 22, 21, 10, tzinfo=timezone.utc)
        patcher = patch.object(f, "CLOSED_CACHE", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _closed(self):
        return [{
            "number": 710, "state": "closed", "title": "accepted",
            "merged_at": "2026-11-22T21:09:00Z",
            "updated_at": "2026-11-22T21:09:01Z",
        }, {
            "number": 711, "state": "closed", "title": "withdrawn",
            "merged_at": None, "updated_at": "2026-11-22T21:09:04Z",
        }]

    def test_correct_merge_vs_closed_status_and_owner_only_cache(self):
        with patch.object(f, "_fetch_closed_rest",
                          return_value=f._closed_from_response(self._closed())) as fetch:
            first = f._github_closed_history(self.now)
            again = f._github_closed_history(self.now + timedelta(minutes=10))
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(first["snapshot_fresh"])
        self.assertTrue(again["snapshot_fresh"])
        self.assertEqual(first["merged_in_visible_sample"], 1)
        self.assertEqual(first["closed_unmerged_in_visible_sample"], 1)
        self.assertEqual([x["outcome"] for x in first["recent"]],
                         ["merged", "closed-unmerged"])
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)

    def test_stale_cache_is_labelled_stale_on_public_api_failure(self):
        with patch.object(f, "_fetch_closed_rest",
                          return_value=f._closed_from_response(self._closed())):
            f._github_closed_history(self.now)
        with patch.object(f, "_fetch_closed_rest", side_effect=TimeoutError("offline")):
            status = f._github_closed_history(self.now + timedelta(minutes=17))
        self.assertFalse(status["snapshot_fresh"])
        self.assertEqual(status["merged_in_visible_sample"], 1)

    def test_recursive_public_response_uses_stale_cache_without_crash(self):
        with patch.object(f, "_fetch_closed_rest",
                          return_value=f._closed_from_response(self._closed())):
            f._github_closed_history(self.now)
        # A bounded JSON response can still exceed the decoder recursion limit.
        with patch.object(f, "_fetch_closed_rest",
                          side_effect=RecursionError("deep public JSON")):
            status = f._github_closed_history(self.now + timedelta(minutes=17))
        self.assertFalse(status["snapshot_fresh"])
        self.assertEqual(status["merged_in_visible_sample"], 1)

    def test_recursive_private_cache_decode_does_not_take_down_other_sources(self):
        # Decoder recursion thresholds vary across CPython versions; explicitly
        # simulate json.loads rejecting an over-deep local cache payload.
        with (patch.object(f, "_load_closed_cache",
                           side_effect=RecursionError("deep private JSON")),
              patch.object(f, "_cezar", return_value={"active": 1}),
              patch.object(f, "_schedule_list", return_value={"enabled": 5}),
              patch.object(f, "_models", return_value={"inference_busy_known": 0}),
              patch.object(f, "_github", return_value={"open_prs": 12})):
            snap = f.collect(self.now, include_history=True)
        self.assertEqual(snap["cezar"]["active"], 1)
        self.assertEqual(snap["schedules"]["enabled"], 5)
        self.assertEqual(snap["github"]["open_prs"], 12)
        self.assertEqual(snap["models"]["inference_busy_known"], 0)
        self.assertEqual(snap["github_history"],
                         {"available": False, "reason": "RecursionError"})

    def test_absent_cache_and_network_failure_is_unavailable(self):
        with patch.object(f, "_fetch_closed_rest", side_effect=TimeoutError("offline")):
            with self.assertRaises(f.FleetError):
                f._github_closed_history(self.now)

    def test_public_rest_strictly_requires_closed_and_real_numbers(self):
        for broken in (
            [{"number": True, "state": "closed", "title": "x",
              "merged_at": None, "updated_at": "2026-11-22T21:09:00Z"}],
            [{**self._closed()[0], "state": "open"}],
            [{**self._closed()[0], "merged_at": "not-a-date"}],
            self._closed() + [dict(self._closed()[0])],
        ):
            with self.subTest(broken=str(broken)[:100]):
                with self.assertRaises(f.FleetError):
                    f._closed_from_response(broken)

    @unittest.skipUnless(hasattr(os, "mkfifo") and os.name == "posix",
                         "POSIX named pipes required")
    def test_fifo_cache_is_refused_without_waiting_for_writer(self):
        # The subprocess timeout ensures a regression cannot hang the CI worker.
        # Exercise collect(), not only the low-level cache reader: failure must
        # affect GitHub history alone, never healthy Cezar/agent sources.
        os.mkfifo(self.cache, 0o600)
        code = (
            "import importlib.util,sys\n"
            "from datetime import datetime,timezone\n"
            "from pathlib import Path\n"
            "from unittest.mock import patch\n"
            "spec=importlib.util.spec_from_file_location('cezar_cache_check',sys.argv[1])\n"
            "module=importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "module.CLOSED_CACHE=Path(sys.argv[2])\n"
            "with (patch.object(module,'_cezar',return_value={'active':1}),\n"
            "      patch.object(module,'_schedule_list',return_value={'enabled':5}),\n"
            "      patch.object(module,'_models',return_value={'capacity_slots_known':17}),\n"
            "      patch.object(module,'_github',return_value={'open_prs':12})):\n"
            "    snap=module.collect(datetime(2026,11,22,21,10,tzinfo=timezone.utc),\n"
            "                        include_history=True)\n"
            "assert snap['github_history']=={'available':False,'reason':'FleetError'},snap\n"
            "assert snap['cezar']['active']==1,snap\n"
            "assert snap['schedules']['enabled']==5,snap\n"
            "assert snap['models']['capacity_slots_known']==17,snap\n"
            "assert snap['github']['open_prs']==12,snap\n"
            "assert snap['external_running_confirmed'] is None,snap\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(SRC), str(self.cache)],
            capture_output=True, text=True, timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_symlink_or_untrusted_history_cache_is_not_read(self):
        secret = self.parent / "secret.json"
        secret.write_text('{"password":"stay private"}')
        self.cache.symlink_to(secret)
        with self.assertRaises(f.FleetError):
            f._github_closed_history(self.now)
        self.cache.unlink()
        self.cache.write_text("{}")
        self.cache.chmod(0o644)
        with self.assertRaises(f.FleetError):
            f._github_closed_history(self.now)

    def test_live_source_separation_is_enforced(self):
        with patch.object(f, "_get", side_effect=lambda url:
                          [{"status":"cancelled"}] if url.endswith("/runs")
                          else {"available": True, "repo":"Garyu-tt/mojum",
                                "prsTotal": 12, "issuesTotal": 3,
                                "prs":[{"number":712,"title":"new",
                                        "createdAt":"2026-11-22T21:00:00Z"}],
                                "issues":[]}):
            with patch.object(f, "_models", return_value={"capacity_slots_known":17,
                                                            "inference_busy_known":0}):
                with patch.object(f, "_schedule_list", return_value={
                    "enabled":5, "running":None}):
                    with patch.object(f, "_github_closed_history",
                                      return_value={"available":True,
                                                    "recent":[]}) as hist:
                        default = f.collect(self.now)
                        self.assertFalse(default["github_history"]["available"])
                        self.assertEqual(hist.call_count, 0)
                        live = f.collect(self.now, include_history=True)
                        self.assertTrue(live["github_history"]["available"])
                        self.assertEqual(hist.call_count, 1)
                        self.assertEqual(live["cezar"]["total"], 1)
                        self.assertEqual(live["github"]["open_prs"], 12)
                        self.assertEqual(len(live["github"]["latest_open_prs"]), 1)
                        self.assertIsNone(live["external_running_confirmed"])

    def test_malicious_title_is_renderable_text_only(self):
        rows = f._public_summary([
            {"number": 712, "title": "<script>alert(1)</script>\u0000",
             "createdAt":"2026-11-22T20:50:00Z"}
        ], max_items=5, kind="pr")
        self.assertEqual(rows[0]["number"], 712)
        self.assertNotIn("\u0000", rows[0]["title"])
        self.assertEqual(rows[0]["title"], "<script>alert(1)</script> ")


if __name__=="__main__":
    unittest.main()
