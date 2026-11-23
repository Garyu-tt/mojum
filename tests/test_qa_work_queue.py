from concurrent.futures import ThreadPoolExecutor

import pytest

from mojum.qa_work_queue import IdempotencyConflict, LeaseLost, QAWorkQueue, QueueError


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
        old = q.claim("slow", lease_seconds=5, now=2)
        new = q.claim("replacement", lease_seconds=10, now=8)
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
