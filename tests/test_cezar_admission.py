"""Cezar cross-rule admission: concurrent claims, fail-closed dispatch, no replay."""
from __future__ import annotations
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import uuid
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "tools" / "cezar_admission.py"
spec = importlib.util.spec_from_file_location("cezar_admission", PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "private"
        self.home.mkdir(mode=0o700)
        self.db_path = self.home / "admission.sqlite"
        self.db = m.connect(self.db_path)
        self.addCleanup(self.db.close)
        self.revision = "a" * 40

    def ident(self, **updates):
        base = {
            "repo": "Garyu-tt/mojum", "kind": "pull", "number": 612,
            "revision": self.revision, "phase": "review", "scope": "mojum/pr612",
        }
        base.update(updates)
        return m.identity(**base)

    def private_prompt(self, text="Review PR #612 head revision, provide tests to run."):
        p = self.home / "prompt.txt"
        p.write_text(text, encoding="utf-8")
        p.chmod(0o600)
        return p

    def test_claim_same_event_twice_dedupes_even_across_rules(self):
        ident = self.ident()
        first, row1 = m.claim(self.db, ident)
        second, row2 = m.claim(self.db, ident)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(row1["id"], row2["id"])
        self.assertEqual(row2["status"], "claimed")
        self.assertEqual(row1["key"], ident["key"])

    def test_revision_change_is_distinct_work(self):
        one, _ = m.claim(self.db, self.ident())
        two, _ = m.claim(self.db, self.ident(revision="b" * 40))
        self.assertTrue(one and two)
        self.assertEqual(self.db.execute("SELECT count(*) FROM admission").fetchone()[0], 2)

    def test_phase_change_is_distinct_intent(self):
        one, _ = m.claim(self.db, self.ident(phase="build"))
        two, _ = m.claim(self.db, self.ident(phase="review"))
        self.assertTrue(one and two)

    def test_conflicting_scope_same_revision_rejected(self):
        m.claim(self.db, self.ident())
        with self.assertRaises(m.AdmissionError):
            m.claim(self.db, self.ident(scope="other/intent"))

    def test_isolation_between_github_entities(self):
        x = self.ident()
        y = self.ident(kind="issue")
        z = self.ident(number=613)
        self.assertEqual(len({x["key"], y["key"], z["key"]}), 3)

    def test_invalid_identity_fails_closed(self):
        bad = [
            {"repo": "other/repo"}, {"repo": None}, {"repo": 123}, {"kind": "unknown"}, {"number": 0},
            {"number": True}, {"number": -1}, {"phase": "admin"},
            {"scope": "../traversal"}, {"scope": ""},
            {"revision": "abc"}, {"revision": "z" * 40},
        ]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(m.AdmissionError):
                self.ident(**item)

    def test_claim_is_atomic_across_sqlite_connections(self):
        ids = []
        errors = []
        barrier = threading.Barrier(6)

        def worker():
            try:
                db = m.connect(self.db_path)
                try:
                    barrier.wait(timeout=8)
                    exists, record = m.claim(db, self.ident())
                    ids.append((exists, record["id"]))
                finally:
                    db.close()
            except Exception as e:
                errors.append(str(e))
        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=12)
        self.assertFalse(errors)
        self.assertEqual(len(ids), 6)
        self.assertEqual(sum(a for a, _ in ids), 1)
        self.assertEqual(len({rid for _, rid in ids}), 1)

    def test_second_distinct_dispatch_waits_for_unresolved_run(self):
        # The CLI/manual claim API can record independent intent, but POST
        # dispatch has one global unresolved slot even across revisions.
        prompt = self.private_prompt()
        class Response:
            status = 201
            def geturl(self): return m._LOCAL_CEZAR
            def read(self, n): return json.dumps({"id": str(uuid.uuid4())}).encode()
            def __enter__(self): return self
            def __exit__(self, *args): return False
        class Client:
            calls = 0
            def open(self, request, timeout=8):
                self.calls += 1
                return Response()
        client = Client()
        with patch.object(m.urllib.request, "build_opener", return_value=client):
            first = m.dispatch(self.db, self.ident(), prompt)
            with self.assertRaisesRegex(m.AdmissionError, "unresolved"):
                m.dispatch(self.db, self.ident(number=613), prompt)
            self.assertEqual(client.calls, 1)
            self.assertEqual(self.db.execute(
                "SELECT count(*) FROM admission").fetchone()[0], 1)
            m.mark_blocked(self.db, first["admission_id"], first["run_id"])
            second = m.dispatch(self.db, self.ident(number=613), prompt)
        self.assertTrue(second["admitted"])
        self.assertEqual(client.calls, 2)

    def test_two_distinct_dispatchers_racing_admit_only_one_http_post(self):
        # Both callers pass the old pending() preflight, proving that the
        # capacity decision must be made inside claim's BEGIN IMMEDIATE.
        prompt = self.private_prompt()
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        reports = []
        errors = []
        class Response:
            status = 201
            def geturl(self): return m._LOCAL_CEZAR
            def read(self, n): return json.dumps({"id": str(uuid.uuid4())}).encode()
            def __enter__(self): return self
            def __exit__(self, *args): return False
        class Client:
            calls = 0
            def open(self, request, timeout=8):
                with lock:
                    self.calls += 1
                return Response()
        client = Client()
        def worker(number):
            try:
                db = m.connect(self.db_path)
                try:
                    barrier.wait(timeout=8)
                    try:
                        result = m.dispatch(db, self.ident(number=number), prompt)
                        with lock:
                            reports.append(result["admitted"])
                    except m.AdmissionError as exc:
                        with lock:
                            errors.append(str(exc))
                finally:
                    db.close()
            except Exception as exc:
                with lock:
                    errors.append(repr(exc))
        with patch.object(m.urllib.request, "build_opener", return_value=client):
            threads = [threading.Thread(target=worker, args=(n,)) for n in (612, 613)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=12)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(reports, [True])
        self.assertEqual(len(errors), 1)
        self.assertIn("unresolved Cezar task", errors[0])
        self.assertEqual(client.calls, 1)
        self.assertEqual(self.db.execute(
            "SELECT count(*) FROM admission WHERE status IN ('claimed','submitted')"
        ).fetchone()[0], 1)

    def test_private_state_permissions_and_symlink_refusal(self):
        self.assertEqual(self.db_path.stat().st_mode & 0o777, 0o600)
        pub = Path(self.tmp.name) / "public"
        pub.mkdir(mode=0o755)
        pub.chmod(0o755)
        with self.assertRaises(m.AdmissionError):
            m.connect(pub / "admission.sqlite")
        link = Path(self.tmp.name) / "alias"
        link.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(m.AdmissionError):
            m.connect(link / "admission.sqlite")
        f = self.home / "bad.sqlite"
        f.write_text("not sqlite")
        f.chmod(0o644)
        with self.assertRaises(m.AdmissionError):
            m.connect(f)

    def test_mark_submitted_and_idempotency(self):
        _, row = m.claim(self.db, self.ident())
        run_id = str(uuid.uuid4())
        result = m.mark_submitted(self.db, row["id"], run_id)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["run_id"], run_id)
        self.assertEqual(m.mark_submitted(self.db, row["id"], run_id)["run_id"], run_id)
        with self.assertRaises(m.AdmissionError):
            m.mark_submitted(self.db, row["id"], str(uuid.uuid4()))
        self.assertEqual(m.claim(self.db, self.ident())[0], False)

    def test_blocking_rejected_model_result_is_terminal_and_idempotent(self):
        ident = self.ident()
        _, row = m.claim(self.db, ident)
        run_id = str(uuid.uuid4())
        submitted = m.mark_submitted(self.db, row["id"], run_id)
        blocked = m.mark_blocked(self.db, row["id"], run_id)
        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(m.mark_blocked(self.db, row["id"], run_id)["status"], "blocked")
        retry, old = m.claim(self.db, ident)
        self.assertFalse(retry)
        self.assertEqual(old["status"], "blocked")

    def test_block_wrong_run_id_or_unsubmitted_claim_refused(self):
        _, row = m.claim(self.db, self.ident())
        run_id = str(uuid.uuid4())
        with self.assertRaises(m.AdmissionError):
            m.mark_blocked(self.db, row["id"], run_id)
        m.mark_submitted(self.db, row["id"], run_id)
        with self.assertRaises(m.AdmissionError):
            m.mark_blocked(self.db, row["id"], str(uuid.uuid4()))
        with self.assertRaises(m.AdmissionError):
            m.mark_blocked(self.db, str(uuid.uuid4()), run_id)
        self.assertEqual(self.db.execute("SELECT status FROM admission").fetchone()[0], "submitted")

    def test_cli_block_does_not_echo_prompts_or_auto_requeue(self):
        _, row = m.claim(self.db, self.ident())
        run_id = str(uuid.uuid4())
        m.mark_submitted(self.db, row["id"], run_id)
        output = io.StringIO()
        with patch("sys.stdout", output):
            rc = m.main(["--db", str(self.db_path), "block", "--admission-id", row["id"],
                         "--run-id", run_id])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "blocked")
        self.assertEqual(self.db.execute("SELECT count(*) FROM admission").fetchone()[0], 1)

    def test_completed_claim_never_implicitly_requeues(self):
        _, row = m.claim(self.db, self.ident())
        self.db.execute("UPDATE admission SET status='finished' WHERE id=?", (row["id"],))
        duplicate, old = m.claim(self.db, self.ident())
        self.assertFalse(duplicate)
        self.assertEqual(old["status"], "finished")

    def test_dispatch_refuses_nonprivate_prompt(self):
        p = self.private_prompt()
        p.chmod(0o644)
        with self.assertRaises(m.AdmissionError):
            m.dispatch(self.db, self.ident(), p)
        self.assertEqual(self.db.execute("SELECT count(*) FROM admission").fetchone()[0], 0)

    def test_dispatch_refuses_nonloopback_endpoint(self):
        p = self.private_prompt()
        with self.assertRaises(m.AdmissionError):
            m.dispatch(self.db, self.ident(), p, url="https://example.com/run")

    def test_dispatch_posts_read_only_and_records_single_run(self):
        p = self.private_prompt()
        run_id = str(uuid.uuid4())
        class Response:
            status = 201
            def __init__(self, url): self.url = url
            def geturl(self): return self.url
            def read(self, n): return json.dumps({"id": run_id}).encode()
            def __enter__(self): return self
            def __exit__(self, *args): return False
        class Client:
            def __init__(self): self.calls = 0; self.body = None
            def open(self, req, timeout=8):
                self.calls += 1
                self.body = json.loads(req.data)
                self.asserted_url = req.full_url
                return Response(req.full_url)
        client = Client()
        with patch.object(m.urllib.request, "build_opener", return_value=client):
            result = m.dispatch(self.db, self.ident(), p)
            duplicate = m.dispatch(self.db, self.ident(), p)
        self.assertTrue(result["admitted"])
        self.assertFalse(duplicate["admitted"])
        self.assertEqual(result["run_id"], run_id)
        self.assertEqual(client.calls, 1)
        self.assertEqual(client.body["readOnly"], True)
        self.assertEqual(client.body["enforceReadOnly"], True)
        self.assertEqual(client.body["autonomous"], False)
        self.assertFalse(client.body["generateFollowups"])
        self.assertEqual(client.body["runner"], "opencode")
        self.assertEqual(client.asserted_url, "http://127.0.0.1:4329/api/v1/runs")

    def test_uncertain_dispatch_keeps_claim_without_retry(self):
        p = self.private_prompt()
        class Client:
            calls = 0
            def open(self, *args, **kwargs):
                self.calls += 1
                raise urllib.error.URLError("timeout")
        client = Client()
        with patch.object(m.urllib.request, "build_opener", return_value=client):
            with self.assertRaises(m.AdmissionError):
                m.dispatch(self.db, self.ident(), p)
            again = m.dispatch(self.db, self.ident(), p)
        self.assertFalse(again["admitted"])
        self.assertEqual(client.calls, 1)
        row = self.db.execute("SELECT status,run_id FROM admission").fetchone()
        self.assertEqual(row["status"], "claimed")
        self.assertIsNone(row["run_id"])

    def test_untrusted_prompt_length_bounded(self):
        p = self.private_prompt("x" * (m.MAX_PROMPT + 1))
        with self.assertRaises(m.AdmissionError):
            m.dispatch(self.db, self.ident(), p)
        self.assertEqual(self.db.execute("SELECT count(*) FROM admission").fetchone()[0], 0)

    def test_cli_no_prompt_echo_and_stats(self):
        out = io.StringIO()
        with patch("sys.stdout", out):
            rc = m.main(["--db", str(self.db_path), "claim", "--kind", "pull",
                         "--number", "612", "--revision", self.revision,
                         "--phase", "review", "--scope", "mojum/pr612"])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out.getvalue())["admitted"])
        out = io.StringIO()
        with patch("sys.stdout", out):
            rc = m.main(["--db", str(self.db_path), "stats"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["statuses"], {"claimed": 1})
        self.assertNotIn("mojum/pr612", out.getvalue())


if __name__ == "__main__":
    unittest.main()
