"""Hermetic fail-closed reconciliation tests for Cezar GitHub intake."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import uuid

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("cezar_reconcile", TOOLS / "cezar_reconcile.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        private = Path(self.tmp.name) / "private"
        private.mkdir(mode=0o700)
        self.db = r.admission.connect(private / "admission.sqlite")
        self.addCleanup(self.db.close)
        self.now = datetime.now(timezone.utc)
        self.serial = 612

    def _record(self, *, status="submitted", phase="review", age=0, number=None):
        num = number or self.serial
        self.serial += 1
        ident = r.admission.identity("Garyu-tt/mojum", "pull", num, "c"*40, phase, "cezar/review")
        _, row = r.admission.claim(self.db, ident)
        run_id = str(uuid.uuid4())
        if status == "submitted":
            r.admission.mark_submitted(self.db, row["id"], run_id)
        self.db.execute("UPDATE admission SET updated_at=? WHERE id=?",
                        ((self.now-timedelta(seconds=age)).isoformat(),row["id"]))
        return row["id"],run_id

    def _get(self, run_id, status):
        return lambda rid: {"id": rid, "status": status}

    def _status(self, admission_id):
        return self.db.execute("SELECT status FROM admission WHERE id=?", (admission_id,)).fetchone()[0]

    def test_failed_run_blocked_once_without_new_launch(self):
        admission_id, run_id = self._record(age=12)
        result = r.reconcile(self.db, now=self.now, get_run=self._get(run_id,"failed"))
        self.assertEqual((result["blocked"],result["accepted"]),(1,0))
        self.assertEqual(self._status(admission_id),"blocked")
        again = r.reconcile(self.db, now=self.now, get_run=self._get(run_id,"failed"))
        self.assertEqual((again["inspected"],again["blocked"]),(0,0))

    def test_cancelled_run_reconciles_without_cancel_again(self):
        admission_id, run_id = self._record(age=1)
        def never_cancel(_): raise AssertionError("should not cancel terminal run")
        result=r.reconcile(self.db,now=self.now,get_run=self._get(run_id,"cancelled"),cancel_run=never_cancel)
        self.assertEqual(result["blocked"],1)
        self.assertEqual(self._status(admission_id),"blocked")

    def test_queued_and_running_under_threshold_are_not_cancelled(self):
        for status in ("queued","running","starting"):
            admission_id, run_id=self._record(age=60)
            result=r.reconcile(self.db,now=self.now,get_run=self._get(run_id,status),
                               cancel_run=lambda _:self.fail("unexpected cancellation"))
            self.assertGreaterEqual(result["still_active"],1)
            self.assertEqual(self._status(admission_id),"submitted")

    def test_waiting_for_qa_is_not_autoaccepted(self):
        admission_id, run_id=self._record(age=120)
        result=r.reconcile(self.db,now=self.now,get_run=self._get(run_id,"waiting"),
                           cancel_run=lambda _:self.fail("not old enough to cancel"))
        self.assertEqual(result["awaiting_qa"],1)
        self.assertEqual(result["accepted"],0)
        self.assertEqual(self._status(admission_id),"submitted")

    def test_stale_waiting_run_cancelled_then_blocked(self):
        admission_id, run_id=self._record(age=601)
        state={"current":"waiting","cancelled":0}
        def fetch(ident): return {"id":ident,"status":state["current"]}
        def cancel(ident):
            self.assertEqual(ident,run_id)
            state["current"]="cancelled"
            state["cancelled"]+=1
            return {"cancelled":True}
        out=r.reconcile(self.db,now=self.now,get_run=fetch,cancel_run=cancel)
        self.assertEqual(out["cancelled"],1)
        self.assertEqual(out["blocked"],1)
        self.assertEqual(state["cancelled"],1)
        self.assertEqual(self._status(admission_id),"blocked")

    def test_stale_running_run_cancelled_after_30_minutes(self):
        admission_id,run_id=self._record(age=1801)
        state={"current":"running"}
        def fetch(i):return {"id":i,"status":state["current"]}
        def cancel(i):state["current"]="cancelled"
        out=r.reconcile(self.db,now=self.now,get_run=fetch,cancel_run=cancel)
        self.assertEqual(out["blocked"],1)
        self.assertEqual(self._status(admission_id),"blocked")

    def test_stale_completion_requires_qa_and_is_blocked(self):
        admission_id,run_id=self._record(age=601)
        out=r.reconcile(self.db,now=self.now,get_run=self._get(run_id,"completed"))
        self.assertEqual(out["blocked"],1)
        self.assertEqual(out["accepted"],0)
        self.assertEqual(self._status(admission_id),"blocked")

    def test_unverified_cancellation_does_not_block(self):
        admission_id,run_id=self._record(age=601)
        out=r.reconcile(self.db,now=self.now,get_run=self._get(run_id,"waiting"),
                        cancel_run=lambda _: {"cancelled":True})
        self.assertEqual(out["blocked"],0)
        self.assertEqual(out["awaiting_qa"],1)
        self.assertEqual(self._status(admission_id),"submitted")

    def test_ambiguous_claim_left_untouched_even_when_stale(self):
        admission_id,_=self._record(status="claimed",age=99999)
        out=r.reconcile(self.db,now=self.now,get_run=lambda _:self.fail("no run ID"))
        self.assertEqual(out["uncertain_claims"],1)
        self.assertEqual(self._status(admission_id),"claimed")

    def test_unknown_state_fails_closed(self):
        admission_id,run_id=self._record(age=2)
        with self.assertRaises(r.admission.AdmissionError):
            r.reconcile(self.db,now=self.now,get_run=self._get(run_id,"alien"))
        self.assertEqual(self._status(admission_id),"submitted")

    def test_future_timestamp_fails_closed(self):
        admission_id,run_id=self._record(age=-300)
        with self.assertRaises(r.admission.AdmissionError):
            r.reconcile(self.db,now=self.now,get_run=self._get(run_id,"failed"))
        self.assertEqual(self._status(admission_id),"submitted")

    def test_wrong_run_id_bad_http_response_refused(self):
        run_id=str(uuid.uuid4())
        class FakeResponse:
            status=200
            def __init__(self,url):self.url=url
            def geturl(self):return self.url
            def read(self,n):return json.dumps({"id":str(uuid.uuid4()),"status":"failed"}).encode()
            def __enter__(self):return self
            def __exit__(self,*args):return False
        class FakeOpen:
            def open(self,req,timeout):return FakeResponse(req.full_url)
        with self.assertRaises(r.admission.AdmissionError):
            r._request(run_id,opener=FakeOpen())
        with self.assertRaises(r.admission.AdmissionError):
            r._request("not-a-uuid",opener=FakeOpen())

    def test_maximum_pending_rows_fail_closed(self):
        for _ in range(r.MAX_ROWS+1):self._record()
        with self.assertRaises(r.admission.AdmissionError):
            r.reconcile(self.db,now=self.now,get_run=lambda _:self.fail("over capacity"))

    def test_thresholds_cannot_be_reversed(self):
        with self.assertRaises(r.admission.AdmissionError):
            r.reconcile(self.db,now=self.now,wait_seconds=1900,run_seconds=800)


if __name__ == "__main__":
    unittest.main()
