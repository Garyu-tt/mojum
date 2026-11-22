"""Hermetic tests for bounded 17-slot Cezar QA read-only triage."""
from __future__ import annotations
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
SRC=HERE/"qa_fleet.py" if (HERE/"qa_fleet.py").exists() else Path("/var/lib/cezar/home/qa-fleet-r01.py")
spec=importlib.util.spec_from_file_location("qa_fleet_tested",SRC)
fleet=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fleet)

class QAFleetTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        root=Path(self.t.name)
        self.home=root/"fleet"
        self.home.mkdir(mode=0o700)
        self.mirror=root/"mirror.json"
        self.fake_items=[{"number":n,"kind":"pull_request","title":f"fixture PR {n}",
            "url":f"https://github.com/Garyu-tt/mojum/pull/{n}",
            "updated_at":"2026-11-22T19:00:00Z"} for n in (1,2,3)]
        self.write_mirror()
        self.patch("HOME",self.home)
        self.patch("SNAPSHOT",self.home/"current.json")
        self.patch("HISTORY",self.home/"history.json")
        self.patch("LOCK",self.home/"lock")
        self.patch("MIRROR",self.mirror)

    def patch(self,name,value):
        p=patch.object(fleet,name,value)
        p.start()
        self.addCleanup(p.stop)

    def write_mirror(self):
        blob={"schema":"mojum.cezar.github-public-mirror.v1",
              "repository":"Garyu-tt/mojum","possibly_truncated":False,
              "fetched_at_utc":datetime.now(timezone.utc).isoformat(),
              "pull_requests_open":len(self.fake_items),"issues_open":0,
              "items":self.fake_items}
        self.mirror.write_text(json.dumps(blob),encoding="utf-8")
        self.mirror.chmod(0o600)

    def test_inventory_resolves_exact_public_pr_ids(self):
        self.assertEqual({x["number"] for x in fleet.local_backlog()},{1,2,3})
        self.assertIsNone(fleet.local_backlog()[0]["isDraft"])

    def test_no_duplicate_work_after_successful_cycle(self):
        with patch.object(fleet,"fetch_diff",return_value=("diff --git a/a b/a", "digest")), \
             patch.object(fleet,"model",return_value=("One concrete test",{"total_tokens":20})) as model:
            self.assertEqual(fleet.main(["--max-workers","3"]),0)
            result=json.loads((self.home/"current.json").read_text())
            self.assertEqual(result["assigned"],3)
            self.assertEqual(result["completed_tasks"],3)
            self.assertEqual(len({x["slot"] for x in result["workers"]}),17)
            self.assertEqual(len({x["pr"] for x in result["workers"] if x["pr"]}),3)
            self.assertEqual(fleet.main(["--max-workers","3"]),0)
            self.assertEqual(model.call_count,3)
            seen=json.loads((self.home/"history.json").read_text())["seen"]
            self.assertEqual(len(seen),3)

    def test_failed_pr_does_not_stop_other_workers(self):
        def model(port,prompt):
            if "PR #2:" in prompt: raise TimeoutError("fixture")
            return "One concrete test",{}
        with patch.object(fleet,"fetch_diff",return_value=("diff --git a/a b/a","digest")), \
             patch.object(fleet,"model",side_effect=model):
            self.assertEqual(fleet.main(["--max-workers","3"]),0)
        snap=json.loads((self.home/"current.json").read_text())
        self.assertEqual(snap["completed_tasks"],2)
        self.assertEqual(snap["failed_tasks"],1)

    def test_mirror_symlink_rejected(self):
        actual=self.mirror.with_name("source.json")
        self.mirror.rename(actual)
        self.mirror.symlink_to(actual)
        with self.assertRaises(OSError):
            fleet.local_backlog()

if __name__=="__main__":
    unittest.main()
