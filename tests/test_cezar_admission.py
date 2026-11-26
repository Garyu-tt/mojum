"""Cezar cross-rule admission: concurrent claims, fail-closed dispatch, no replay."""
from __future__ import annotations
from contextlib import closing
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

    def test_concurrent_distinct_intakes_have_one_atomic_capacity_slot(self):
        """Two different revisions must not each reserve the one Cezar run."""
        barrier = threading.Barrier(2)
        results, errors = [], []
        lock = threading.Lock()

        def worker(number):
            try:
                db = m.connect(self.db_path)
                try:
                    barrier.wait(timeout=8)
                    fresh, row = m.claim(
                        db, self.ident(number=number), single_flight=True)
                    with lock:
                        results.append((fresh, row["status"]))
                finally:
                    db.close()
            except Exception as exc:
                with lock:
                    errors.append(str(exc))

        workers = [threading.Thread(target=worker, args=(n,)) for n in (612, 613)]
        for t in workers:
            t.start()
        for t in workers:
            t.join(timeout=15)
        self.assertFalse(any(t.is_alive() for t in workers))
        self.assertEqual(errors, [])
        self.assertCountEqual(results, [(True, "claimed"), (False, "busy")])
        self.assertEqual(self.db.execute(
            "SELECT count(*) FROM admission WHERE status IN ('claimed','submitted')"
        ).fetchone()[0], 1)

    def test_dispatch_busy_does_not_post_second_distinct_run(self):
        m.claim(self.db, self.ident(number=612))
        prompt = self.private_prompt()
        with patch.object(m.urllib.request, "build_opener",
                          side_effect=AssertionError("unexpected second POST")):
            result = m.dispatch(self.db, self.ident(number=613), prompt)
        self.assertFalse(result["admitted"])
        self.assertEqual(result["reason"], "capacity-busy")
        self.assertEqual(result["status"], "busy")
        self.assertEqual(self.db.execute(
            "SELECT count(*) FROM admission").fetchone()[0], 1)

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

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_DIRECTORY"),
                         "POSIX directory fsync required")
    def test_sqlite_creation_fsyncs_new_inode_then_private_parent(self):
        path = self.home / "new-durable.sqlite"
        original = os.fsync
        calls = []

        def record(fd):
            mode = m.stat.S_IFMT(os.fstat(fd).st_mode)
            calls.append("file" if mode == m.stat.S_IFREG else
                         "directory" if mode == m.stat.S_IFDIR else "other")
            return original(fd)

        with patch.object(m.os, "fsync", side_effect=record):
            with closing(m.connect(path)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM admission").fetchone()[0], 0)
        self.assertEqual(calls, ["file", "directory"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

        calls.clear()
        with patch.object(m.os, "fsync", side_effect=record):
            with closing(m.connect(path)):
                pass
        self.assertEqual(calls, ["directory"])

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_DIRECTORY"),
                         "POSIX directory fsync required")
    def test_sqlite_durability_sync_failure_denies_new_and_existing_db(self):
        path = self.home / "sync-failure.sqlite"
        with patch.object(m.os, "fsync", side_effect=OSError("sync failure")):
            with self.assertRaisesRegex(OSError, "sync failure"):
                m.connect(path)
        self.assertTrue(path.exists())
        # Failed initial file fsync cannot be bypassed just because the file
        # survived long enough to be reopened.
        with patch.object(m.os, "fsync", side_effect=OSError("sync failure")):
            with self.assertRaisesRegex(OSError, "sync failure"):
                m.connect(path)

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

    def test_manual_finish_releases_capacity_but_not_revision_identity(self):
        ident = self.ident()
        fresh, row = m.claim(self.db, ident, single_flight=True)
        self.assertTrue(fresh)
        run_id = str(uuid.uuid4())
        m.mark_submitted(self.db, row["id"], run_id)
        admitted, busy = m.claim(
            self.db, self.ident(number=613), single_flight=True)
        self.assertFalse(admitted)
        self.assertEqual(busy["status"], "busy")
        with self.assertRaises(m.AdmissionError):
            m.mark_finished(self.db, row["id"], str(uuid.uuid4()))
        self.assertEqual(self.db.execute(
            "SELECT status FROM admission WHERE id=?", (row["id"],)
        ).fetchone()[0], "submitted")
        done = m.mark_finished(self.db, row["id"], run_id)
        self.assertEqual(done["status"], "finished")
        self.assertEqual(m.mark_finished(self.db, row["id"], run_id)["status"], "finished")
        retry, old = m.claim(self.db, ident, single_flight=True)
        self.assertFalse(retry)
        self.assertEqual(old["status"], "finished")
        admitted, other = m.claim(
            self.db, self.ident(number=613), single_flight=True)
        self.assertTrue(admitted)
        self.assertEqual(other["status"], "claimed")

    def test_manual_finish_rejects_unsubmitted_blocked_and_unknown(self):
        _, row = m.claim(self.db, self.ident())
        run_id = str(uuid.uuid4())
        with self.assertRaises(m.AdmissionError):
            m.mark_finished(self.db, row["id"], run_id)
        m.mark_submitted(self.db, row["id"], run_id)
        m.mark_blocked(self.db, row["id"], run_id)
        with self.assertRaises(m.AdmissionError):
            m.mark_finished(self.db, row["id"], run_id)
        with self.assertRaises(m.AdmissionError):
            m.mark_finished(self.db, str(uuid.uuid4()), run_id)
        with self.assertRaises(m.AdmissionError):
            m.mark_finished(self.db, "not-a-uuid", run_id)
        self.assertEqual(self.db.execute(
            "SELECT status FROM admission WHERE id=?", (row["id"],)
        ).fetchone()[0], "blocked")

    def test_cli_manual_finish_is_explicit_not_automatic(self):
        _, row = m.claim(self.db, self.ident())
        run_id = str(uuid.uuid4())
        m.mark_submitted(self.db, row["id"], run_id)
        output = io.StringIO()
        with patch("sys.stdout", output):
            rc = m.main(["--db", str(self.db_path), "finish",
                         "--admission-id", row["id"], "--run-id", run_id])
        self.assertEqual(rc, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "finished")
        self.assertEqual(result["run_id"], run_id)


    def test_reconciled_claim_releases_slot_but_never_replays(self):
        ident = self.ident()
        fresh, row = m.claim(self.db, ident, single_flight=True)
        self.assertTrue(fresh)
        self.assertFalse(m.claim(self.db, self.ident(number=613), single_flight=True)[0])
        with self.assertRaises(m.AdmissionError):
            m.block_claimed_after_reconciliation(self.db, row["id"], self.revision)
        with self.assertRaises(m.AdmissionError):
            m.block_claimed_after_reconciliation(
                self.db, row["id"], "b" * 40, checked_cezar_runs=True)
        self.assertEqual(self.db.execute(
            "SELECT status FROM admission WHERE id=?", (row["id"],)
        ).fetchone()[0], "claimed")
        done = m.block_claimed_after_reconciliation(
            self.db, row["id"], self.revision, checked_cezar_runs=True)
        self.assertEqual(done["status"], "blocked")
        self.assertIsNone(done["run_id"])
        self.assertEqual(m.block_claimed_after_reconciliation(
            self.db, row["id"], self.revision, checked_cezar_runs=True
        )["status"], "blocked")
        self.assertFalse(m.claim(self.db, ident, single_flight=True)[0])
        self.assertTrue(m.claim(self.db, self.ident(number=613), single_flight=True)[0])

    def test_claim_reconciliation_rejects_submitted_unknown_and_bad_revision(self):
        _, row = m.claim(self.db, self.ident())
        for bad_id, bad_revision in (
            (str(uuid.uuid4()), self.revision),
            ("bad-uuid", self.revision),
            (row["id"], "bad"),
        ):
            with self.assertRaises(m.AdmissionError):
                m.block_claimed_after_reconciliation(
                    self.db, bad_id, bad_revision, checked_cezar_runs=True)
        run_id = str(uuid.uuid4())
        m.mark_submitted(self.db, row["id"], run_id)
        with self.assertRaises(m.AdmissionError):
            m.block_claimed_after_reconciliation(
                self.db, row["id"], self.revision, checked_cezar_runs=True)
        self.assertEqual(self.db.execute(
            "SELECT status FROM admission WHERE id=?", (row["id"],)
        ).fetchone()[0], "submitted")

    def test_cli_block_claimed_requires_manual_acknowledgement(self):
        _, row = m.claim(self.db, self.ident())
        with self.assertRaises(SystemExit) as caught:
            m.main(["--db", str(self.db_path), "block-claimed",
                    "--admission-id", row["id"], "--revision", self.revision])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(self.db.execute(
            "SELECT status FROM admission WHERE id=?", (row["id"],)
        ).fetchone()[0], "claimed")
        output = io.StringIO()
        with patch("sys.stdout", output):
            rc = m.main(["--db", str(self.db_path), "block-claimed",
                         "--admission-id", row["id"], "--revision", self.revision,
                         "--checked-cezar-runs"])
        self.assertEqual(rc, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["run_id"])

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

    def test_dispatch_rejects_symlink_and_fifo_prompt_without_claiming(self):
        p = self.private_prompt()
        alias = self.home / "alias.txt"
        alias.symlink_to(p)
        with self.assertRaises(m.AdmissionError):
            m.dispatch(self.db, self.ident(), alias)
        if hasattr(os, "mkfifo"):
            pipe = self.home / "pipe.txt"
            os.mkfifo(pipe)
            with self.assertRaises(m.AdmissionError):
                m.dispatch(self.db, self.ident(), pipe)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM admission").fetchone()[0], 0)

    def test_dispatch_rejects_symlink_in_prompt_ancestors(self):
        p = self.private_prompt()
        alias = Path(self.tmp.name) / "alias-parent"
        alias.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(m.AdmissionError):
            m.dispatch(self.db, self.ident(), alias / p.name)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM admission").fetchone()[0], 0)

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
        with patch.object(m.urllib.request, "build_opener", return_value=client) as build:
            result = m.dispatch(self.db, self.ident(), p)
            duplicate = m.dispatch(self.db, self.ident(), p)
        self.assertTrue(any(isinstance(h, m.NoRedirect) for h in build.call_args.args))
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

    def test_dispatch_redirect_is_refused_before_followup(self):
        req = m.urllib.request.Request(m._LOCAL_CEZAR, data=b"{}", method="POST")
        with self.assertRaisesRegex(m.AdmissionError, "redirect forbidden"):
            m.NoRedirect().redirect_request(
                req, None, 302, "Found", {"Location": "https://example.invalid/collect"},
                "https://example.invalid/collect")

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
