"""Hermetic Cezar Fleet observer tests: no invented running agents."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import importlib.util
import json
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
                    "prs":[{"createdAt":"2026-11-22T20:50:00Z"},
                           {"createdAt":"2026-11-21T18:00:00Z"}]}
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


if __name__=="__main__":
    unittest.main()
