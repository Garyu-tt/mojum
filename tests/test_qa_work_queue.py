from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import sqlite3

try:
    import pytest
except ImportError:  # Full repository unittest discovery must remain stdlib-only.
    pytest = None

from mojum.qa_work_queue import IdempotencyConflict, LeaseLost, QAWorkQueue, QueueError


def _claim_process(path, worker_number, result_queue):
    try:
        claimed = []
        with QAWorkQueue(path, busy_timeout=10) as q:
            while True:
                lease = q.claim(f"process-{worker_number}", lease_seconds=300, now=2)
                if lease is None:
                    break
                claimed.append(lease.job.id)
        result_queue.put(("ok", claimed))
    except BaseException as exc:
        result_queue.put(("error", repr(exc)))


def test_huge_numeric_configuration_raises_queue_error():
    with pytest.raises(QueueError):
        QAWorkQueue(":memory:", busy_timeout=10**10000)


def test_enqueue_idempotency_and_detached_payload(tmp_path):
    path = str(tmp_path / "queue.db")
    payload = {"pr": 12, "head": "a" * 40}
    with QAWorkQueue(path) as q:
        first, inserted = q.enqueue("pr12:" + "a" * 40, "review", payload, now=10)
        payload["pr"] = 99
        second, duplicate = q.enqueue("pr12:" + "a" * 40, "review",
                                      {"pr": 12, "head": "a" * 40}, now=20)
        assert inserted and not duplicate
        assert first.id == second.id and second.payload["pr"] == 12
        assert second.created_at == 10
        with pytest.raises(IdempotencyConflict):
            q.enqueue("pr12:" + "a" * 40, "review", {"pr": 13}, now=30)


def test_claim_heartbeat_complete_and_stale_token_fencing(tmp_path):
    with QAWorkQueue(str(tmp_path / "q.db")) as q:
        job, _ = q.enqueue("one", "diff", {"n": 1}, now=100)
        lease = q.claim("worker-A", lease_seconds=10, now=101)
        assert lease and lease.job.attempts == 1
        assert q.heartbeat(job.id, lease.token, lease_seconds=20, now=105) == 125
        assert q.complete(job.id, lease.token, now=106).status == "succeeded"
        with pytest.raises(LeaseLost):
            q.complete(job.id, lease.token, now=107)


def test_expired_lease_reclaimed_and_old_worker_fenced(tmp_path):
    with QAWorkQueue(str(tmp_path / "q.db")) as q:
        job, _ = q.enqueue("reclaim", "review", {}, max_attempts=3, now=1)
        old = q.claim("worker-A", lease_seconds=5, now=2)
        # Simulate a restarted process reusing the same worker identity.
        new = q.claim("worker-A", lease_seconds=10, now=8)
        assert old and new and new.job.id == job.id and new.job.attempts == 2
        with pytest.raises(LeaseLost):
            q.heartbeat(job.id, old.token, now=9)
        assert q.complete(job.id, new.token, now=10).status == "succeeded"


def test_expired_last_attempt_fails_closed(tmp_path):
    with QAWorkQueue(str(tmp_path / "q.db")) as q:
        job, _ = q.enqueue("last", "qa", {}, max_attempts=1, now=1)
        lease = q.claim("worker", lease_seconds=2, now=2)
        assert lease and q.claim("other", now=5) is None
        assert q.get(job.id).status == "failed"
        assert q.get(job.id).last_error_code == "LEASE_EXPIRED"
        with pytest.raises(LeaseLost):
            q.complete(job.id, lease.token, now=6)


def test_retry_delay_and_attempt_budget(tmp_path):
    with QAWorkQueue(str(tmp_path / "q.db")) as q:
        job, _ = q.enqueue("retry", "triage", {}, max_attempts=2, now=10)
        first = q.claim("w1", now=10)
        assert first
        queued = q.retry(job.id, first.token, delay_seconds=5, now=11)
        assert queued.status == "queued" and queued.available_at == 16
        assert q.claim("w2", now=15) is None
        second = q.claim("w2", now=16)
        assert second
        exhausted = q.retry(job.id, second.token, now=17)
        assert exhausted.status == "failed"


def test_persistence_and_audit_never_copies_payload(tmp_path):
    path = str(tmp_path / "persist.db")
    q = QAWorkQueue(path)
    job, _ = q.enqueue("persist", "ci", {"sensitive_field": "payload-value"}, now=1)
    assert q.claim("worker", now=2)
    q.close()
    with QAWorkQueue(path) as reopened:
        assert reopened.get(job.id).status == "leased"
        events = reopened.audit(job.id)
        assert [item["event"] for item in events] == ["ENQUEUED", "CLAIMED"]
        assert all("payload-value" not in str(item) for item in events)


def test_concurrent_workers_never_double_claim(tmp_path):
    path = str(tmp_path / "race.db")
    with QAWorkQueue(path) as q:
        for i in range(40):
            q.enqueue(f"job-{i}", "independent", {"i": i}, now=1)

    def worker(number):
        claimed = []
        with QAWorkQueue(path, busy_timeout=10) as q:
            while True:
                lease = q.claim(f"worker-{number}", lease_seconds=300, now=2)
                if lease is None:
                    return claimed
                claimed.append(lease.job.id)

    with ThreadPoolExecutor(max_workers=12) as pool:
        batches = list(pool.map(worker, range(12)))
    ids = [job_id for batch in batches for job_id in batch]
    assert len(ids) == 40 and len(set(ids)) == 40
    with QAWorkQueue(path) as q:
        assert q.counts() == {"queued": 0, "leased": 40, "succeeded": 0, "failed": 0}


def test_independent_processes_never_double_claim(tmp_path):
    path = str(tmp_path / "process-race.db")
    with QAWorkQueue(path) as q:
        for i in range(32):
            q.enqueue(f"process-job-{i}", "independent-process", {"i": i}, now=1)

    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    workers = [
        context.Process(target=_claim_process, args=(path, number, results))
        for number in range(6)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    for worker in workers:
        if worker.is_alive():
            worker.terminate()
            worker.join()
            raise AssertionError("worker process did not finish within 30 seconds")
        assert worker.exitcode == 0

    batches = [results.get(timeout=5) for _ in workers]
    errors = [value for status, value in batches if status != "ok"]
    assert not errors, errors
    ids = [job_id for _, batch in batches for job_id in batch]
    assert len(ids) == 32 and len(set(ids)) == 32
    with QAWorkQueue(path) as q:
        assert q.counts() == {"queued": 0, "leased": 32, "succeeded": 0, "failed": 0}


def test_strict_payload_and_option_validation(tmp_path):
    class SneakyDict(dict):
        pass

    with QAWorkQueue(str(tmp_path / "validate.db")) as q:
        for payload in (SneakyDict(a=1), {"a": float("nan")}, {1: "bad"}, {"a": object()}):
            with pytest.raises(QueueError):
                q.enqueue("bad-" + str(id(payload)), "kind", payload)
        with pytest.raises(QueueError):
            q.enqueue("large", "kind", {"data": "x" * 70_000})
        with pytest.raises(QueueError):
            q.enqueue("bool", "kind", {}, max_attempts=True)
        with pytest.raises(QueueError):
            q.claim("worker", lease_seconds=float("inf"))


def test_due_time_filtering_and_wal(tmp_path):
    path = str(tmp_path / "due.db")
    with QAWorkQueue(path) as q:
        later, _ = q.enqueue("later", "kind", {}, available_at=50, now=1)
        ready, _ = q.enqueue("ready", "kind", {}, now=1)
        lease = q.claim("worker", now=10)
        assert lease and lease.job.id == ready.id
        assert [job.id for job in q.list_jobs(status="queued")] == [later.id]
        assert q._db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert q._db.execute("PRAGMA synchronous").fetchone()[0] == 2


def test_invalid_ids_and_limits_rejected(tmp_path):
    with QAWorkQueue(str(tmp_path / "ids.db")) as q:
        with pytest.raises(QueueError):
            q.get(True)
        with pytest.raises(QueueError):
            q.audit(-1)
        with pytest.raises(QueueError):
            q.list_jobs(limit=501)


def test_expired_worker_cannot_heartbeat_before_reclaim(tmp_path):
    with QAWorkQueue(str(tmp_path / "expired.db")) as q:
        job, _ = q.enqueue("expire", "kind", {}, now=1)
        lease = q.claim("worker", lease_seconds=2, now=2)
        assert lease
        with pytest.raises(LeaseLost):
            q.heartbeat(job.id, lease.token, now=4)


def test_read_apis_do_not_disclose_live_lease_token(tmp_path):
    with QAWorkQueue(str(tmp_path / "private-token.db")) as q:
        job, _ = q.enqueue("token-test", "review", {}, now=1)
        lease = q.claim("worker", now=2)
        assert lease is not None and lease.token
        assert not hasattr(q.get(job.id), "lease_token")
        listed = q.list_jobs(status="leased")
        assert len(listed) == 1 and not hasattr(listed[0], "lease_token")
        assert all(lease.token not in repr(event) for event in q.audit(job.id))
        assert q.complete(job.id, lease.token, now=3).status == "succeeded"


def test_schema_version_is_recorded_and_unknown_versions_fail_closed(tmp_path):
    path = str(tmp_path / "versioned.db")
    q = QAWorkQueue(path)
    version = q._db.execute(
        "SELECT value FROM qa_meta WHERE key='schema_version'"
    ).fetchone()[0]
    assert version == "mojum.qa-work-queue/1"
    q._db.execute(
        "UPDATE qa_meta SET value=? WHERE key='schema_version'",
        ("mojum.qa-work-queue/999",),
    )
    q.close()
    with pytest.raises(QueueError, match="unsupported queue schema version"):
        QAWorkQueue(path)


def test_unversioned_existing_database_is_not_adopted(tmp_path):
    path = str(tmp_path / "legacy.db")
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE qa_jobs (legacy INTEGER)")
    connection.commit()
    connection.close()
    with pytest.raises(QueueError, match="unversioned queue database"):
        QAWorkQueue(path)


def test_payload_text_budget_precedes_serialization(tmp_path, monkeypatch):
    with QAWorkQueue(str(tmp_path / "bounded-text.db")) as q:
        def forbidden_serialize(*args, **kwargs):
            raise AssertionError("serializer must not run for oversized text")
        monkeypatch.setattr("mojum.qa_work_queue.json.dumps", forbidden_serialize)
        with pytest.raises(QueueError, match="text exceeds byte budget"):
            q.enqueue("oversized", "review", {"data": "x" * 65_537})


def test_payload_rejects_malformed_unicode_and_oversized_integer(tmp_path):
    with QAWorkQueue(str(tmp_path / "unicode-bounds.db")) as q:
        with pytest.raises(QueueError, match="valid UTF-8"):
            q.enqueue("bad-unicode", "review", {"value": "\ud800"})
        with pytest.raises(QueueError, match="valid UTF-8"):
            q.enqueue("bad-key", "review", {"\ud800": "value"})
        with pytest.raises(QueueError, match="integer exceeds bit budget"):
            q.enqueue("big-int", "review", {"value": 1 << 4096})


def test_text_and_numeric_bounds_precede_expensive_operations(tmp_path):
    class SneakyText(str):
        def strip(self):
            raise AssertionError("subclass method must not execute")

    with QAWorkQueue(str(tmp_path / "bounds.db")) as q:
        with pytest.raises(QueueError):
            q.enqueue("x" * 1_000_000, "review", {})
        with pytest.raises(QueueError):
            q.enqueue(SneakyText("idempotency"), "review", {})
        with pytest.raises(QueueError):
            q.enqueue("huge-time", "review", {}, available_at=10 ** 10_000)
        with pytest.raises(QueueError):
            q.claim("worker", lease_seconds=10 ** 10_000)

    with pytest.raises(QueueError):
        QAWorkQueue(str(tmp_path / "huge-timeout.db"), busy_timeout=10 ** 10_000)


def test_in_memory_database_is_rejected(tmp_path):
    with pytest.raises(QueueError, match="filesystem-backed"):
        QAWorkQueue(":memory:")
