"""Durable QA queue restart, lease, idempotency and concurrency tests."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
import gc
import warnings
from mojum.qa_supervisor_queue import DurableQAQueue, LeaseError, TransitionError, QueueError, MAX_PAYLOAD_BYTES

SHA="a"*40

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.tmp.name)/"queue.sqlite3")
        self.q=DurableQAQueue(self.path)
    def tearDown(self): self.tmp.cleanup()

    def test_idempotent_enqueue_survives_reopen(self):
        a=self.q.enqueue(12,SHA,{"source":"synthetic"},now=10)
        b=DurableQAQueue(self.path).enqueue(12,SHA,{"source":"synthetic"},now=20)
        self.assertEqual(a.job_id,b.job_id)
        self.assertEqual(b.created_at,10)

    def test_conflicting_duplicate_key_never_creates_second_job(self):
        a=self.q.enqueue(12,SHA,idempotency_key="fixed",now=1)
        with self.assertRaises(QueueError):
            self.q.enqueue(99,"b"*40,idempotency_key="fixed",now=2)
        self.assertEqual(self.q.get(a.job_id).issue_number,12)

    def test_concurrent_workers_get_one_exclusive_lease(self):
        self.q.enqueue(12,SHA,now=1)
        with ThreadPoolExecutor(max_workers=10) as pool:
            results=list(pool.map(lambda n: DurableQAQueue(self.path).claim(f"w{n}",now=2),range(10)))
        claimed=[x for x in results if x is not None]
        self.assertEqual(len(claimed),1)
        self.assertEqual(claimed[0].attempts,1)

    def test_expired_lease_recovered_after_process_restart(self):
        self.q.enqueue(12,SHA,now=1)
        first=self.q.claim("old",lease_seconds=5,now=2)
        second=DurableQAQueue(self.path).claim("new",lease_seconds=5,now=7)
        self.assertEqual(first.job_id,second.job_id)
        self.assertEqual(second.attempts,2)
        self.assertNotEqual(first.lease_token,second.lease_token)
        with self.assertRaises(LeaseError):
            self.q.heartbeat(first.job_id,first.lease_token,now=7)

    def test_heartbeat_requires_live_matching_token(self):
        self.q.enqueue(12,SHA,now=1)
        job=self.q.claim("worker",lease_seconds=5,now=2)
        renewed=self.q.heartbeat(job.job_id,job.lease_token,lease_seconds=10,now=4)
        self.assertEqual(renewed.lease_until,14)
        with self.assertRaises(LeaseError):
            self.q.heartbeat(job.job_id,"forged",now=5)

    def test_transition_and_lease_release(self):
        self.q.enqueue(12,SHA,now=1)
        job=self.q.claim("worker",now=2)
        reviewed=self.q.transition(job.job_id,job.lease_token,"reviewing",now=3)
        self.assertEqual(reviewed.state,"reviewing")
        waiting=self.q.transition(job.job_id,job.lease_token,"waiting_ci",now=4)
        self.assertIsNone(waiting.lease_token)
        self.assertIsNone(self.q.claim("other",now=5))

    def test_deferred_job_can_be_requeued_with_compare_and_swap(self):
        self.q.enqueue(12,SHA,now=1)
        job=self.q.claim("worker",now=2)
        waiting=self.q.transition(job.job_id,job.lease_token,"waiting_ci",now=3)
        queued=self.q.requeue(waiting.job_id,expected_state="waiting_ci",now=4)
        self.assertEqual(queued.state,"discovered")
        self.assertIsNone(queued.lease_token)
        with self.assertRaises(TransitionError):
            self.q.requeue(queued.job_id,expected_state="waiting_ci",now=5)

    def test_invalid_transition_does_not_mutate_state(self):
        self.q.enqueue(12,SHA,now=1)
        job=self.q.claim("worker",now=2)
        with self.assertRaises(TransitionError):
            self.q.transition(job.job_id,job.lease_token,"merged",now=3)
        self.assertEqual(self.q.get(job.job_id).state,"claimed")

    def test_payload_bounds_and_native_types(self):
        with self.assertRaises(ValueError): self.q.enqueue(True,SHA)
        with self.assertRaises(ValueError): self.q.enqueue(12,"not-a-sha")
        with self.assertRaises(ValueError): self.q.enqueue(12,SHA,{"x":"x"*MAX_PAYLOAD_BYTES})
        with self.assertRaises(ValueError): self.q.enqueue(12,SHA,{"x":float("nan")})
        with self.assertRaises(ValueError): self.q.enqueue(12,SHA,{"x":object()})


    def test_repeated_operations_close_sqlite_connections(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            for index in range(20):
                job = self.q.enqueue(100 + index, SHA, now=index + 1)
                self.q.claim(f"worker-{index}", now=index + 2)
                self.q.get(job.job_id)
                self.q.counts()
                gc.collect()
        leaked = [item for item in caught if issubclass(item.category, ResourceWarning)]
        self.assertEqual(leaked, [])



    def test_ready_job_can_record_verified_merge_at_exact_head(self):
        self.q.enqueue(12, SHA, now=1)
        job = self.q.claim("worker", now=2)
        ready = self.q.transition(job.job_id, job.lease_token, "ready", now=3)
        with self.assertRaises(TransitionError):
            self.q.record_merged(ready.job_id, expected_head_sha="b" * 40, now=4)
        merged = self.q.record_merged(ready.job_id, expected_head_sha=SHA, now=5)
        self.assertEqual(merged.state, "merged")
        self.assertIsNone(merged.lease_token)
        self.assertEqual(self.q.counts()["merged"], 1)
        with self.assertRaises(TransitionError):
            self.q.record_merged(ready.job_id, expected_head_sha=SHA, now=6)


    def test_terminal_ready_is_not_claimed_automatically(self):
        self.q.enqueue(12,SHA,now=1)
        job=self.q.claim("worker",now=2)
        ready=self.q.transition(job.job_id,job.lease_token,"ready",now=3)
        self.assertIsNone(ready.lease_token)
        self.assertIsNone(self.q.claim("other",now=4))
        self.assertEqual(self.q.counts()["ready"],1)

if __name__=="__main__": unittest.main()
