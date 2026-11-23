"""Offline, network-free Langfuse observability contract regression (issue #697)."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
import json
import unittest

from tools.spartan_langfuse import (
    SafeEvent, safe_envelope, publish, summarize, validate_event,
    MAX_EVENTS, MAX_TOKENS,
)


KEY = b"safe-fixture-HMAC-key-only-not-a-real-secret-0001"
OTHER_KEY = b"other-fixture-HMAC-key-only-not-a-secret-0002"
SHA = "a" * 40


def provider(**changes):
    return replace(SafeEvent(
        stage="provider_admission", lease_id="synthetic-lease:01",
        attempt=1, fencing_epoch=17, sequence=0, status="success",
        latency_ms=12, input_tokens=16, output_tokens=4,
        cost_micro_usd=120,
    ), **changes)


def ci(**changes):
    return replace(SafeEvent(
        stage="ci_complete", lease_id="synthetic-lease:01", attempt=1,
        fencing_epoch=17, sequence=3, status="success",
        pr_head_sha=SHA, ci_passed=True, latency_ms=1200,
    ), **changes)


def qa(**changes):
    return replace(SafeEvent(
        stage="qa_review", lease_id="synthetic-lease:01", attempt=1,
        fencing_epoch=17, sequence=4, status="failure",
        pr_head_sha=SHA, ci_passed=True, qa_passed=False, latency_ms=80,
    ), **changes)


class RecordingClient:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise ConnectionError("synthetic PRIVATE token do not log")
        yield object()


class LangfuseContractTests(unittest.TestCase):
    def test_disabled_by_default_and_no_client_invocation(self):
        client = RecordingClient()
        self.assertFalse(publish(provider(), client=client, secret=KEY))
        # Disabled mode must not even inspect a malformed payload or client.
        self.assertFalse(publish({"prompt": "secret"}, client=client, secret=KEY))
        self.assertFalse(publish(provider(), enabled=1, client=client, secret=KEY))
        self.assertEqual(client.calls, [])

    def test_true_opt_in_sends_only_whitelisted_primitives(self):
        client = RecordingClient()
        event = ci()
        self.assertTrue(publish(event, enabled=True, client=client, secret=KEY))
        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["as_type"], "span")
        self.assertEqual(call["name"], "mojum.spartan.ci_complete")
        self.assertEqual(call["trace_context"]["trace_id"],
                         call["metadata"]["trace_id"])
        self.assertEqual(call["metadata"]["pr_head_sha"], SHA)
        self.assertTrue(call["metadata"]["ci_passed"])
        self.assertEqual(call["metadata"]["fencing_epoch"], 17)
        self.assertEqual(call["metadata"]["attempt"], 1)
        self.assertNotIn("input", call)
        self.assertNotIn("output", call)
        wire = json.dumps(call, sort_keys=True)
        for forbidden in ("synthetic-lease", "PRIVATE", "prompt", "source",
                          "credential", "organism_id", "worker_host"):
            self.assertNotIn(forbidden, wire)

    def test_replay_correlation_is_keyed_and_exact(self):
        original = safe_envelope(provider(), secret=KEY)
        self.assertEqual(original, safe_envelope(provider(), secret=KEY))
        self.assertEqual(len(original["trace_id"]), 32)
        self.assertEqual(len(original["event_id"]), 32)
        self.assertEqual(original["trace_id"], safe_envelope(ci(), secret=KEY)["trace_id"])
        self.assertNotEqual(original["event_id"],
                            safe_envelope(provider(sequence=1), secret=KEY)["event_id"])
        self.assertNotEqual(original["trace_id"],
                            safe_envelope(provider(fencing_epoch=18), secret=KEY)["trace_id"])
        self.assertNotEqual(original["trace_id"],
                            safe_envelope(provider(attempt=2), secret=KEY)["trace_id"])
        self.assertNotEqual(original["trace_id"],
                            safe_envelope(provider(), secret=OTHER_KEY)["trace_id"])
        self.assertNotEqual(safe_envelope(ci(), secret=KEY)["event_id"],
                            safe_envelope(ci(pr_head_sha="b"*40), secret=KEY)["event_id"])

    def test_extra_fields_never_enter_envelope(self):
        with self.assertRaises(TypeError):
            SafeEvent(stage="qa_review", lease_id="x", attempt=1, fencing_epoch=1,
                      sequence=1, status="success", prompt="secret")
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            provider().lease_id = "mutated"
        allowed = set(safe_envelope(ci(), secret=KEY))
        self.assertEqual(allowed, {
            "schema", "trace_id", "event_id", "stage", "status",
            "attempt", "fencing_epoch", "sequence", "latency_ms",
            "input_tokens", "output_tokens", "cost_micro_usd",
            "pr_head_sha", "ci_passed",
        })

    def test_unsupported_event_types_rejected(self):
        bad = (
            provider(stage="prompt_text"),
            provider(status="PRIVATE stack trace"),
            provider(lease_id="token@internal"),
            provider(lease_id="x" * 200),
            provider(attempt=True),
            provider(attempt=0),
            provider(fencing_epoch=0),
            provider(fencing_epoch=2**63),
            provider(sequence=-1),
            provider(latency_ms=-1),
            provider(input_tokens=MAX_TOKENS + 1),
            provider(cost_micro_usd=True),
            provider(pr_head_sha=SHA),
            provider(ci_passed=True),
            ci(pr_head_sha=None),
            ci(pr_head_sha="A" * 40),
            ci(pr_head_sha="x" * 40),
            ci(ci_passed=None),
            ci(qa_passed=False),
            qa(qa_passed=None),
            qa(ci_passed=None),
        )
        for event in bad:
            with self.subTest(event=repr(event)):
                with self.assertRaises(ValueError):
                    validate_event(event)

    def test_crypto_key_is_required_no_fallback(self):
        for secret in (b"", b"x"*31, b"x"*129, "string-secret", None, True):
            with self.subTest(secret=repr(secret)):
                with self.assertRaises(ValueError):
                    safe_envelope(provider(), secret=secret)

    def test_external_failure_cannot_block_acceptance(self):
        client = RecordingClient(fail=True)
        self.assertFalse(publish(qa(), enabled=True, client=client, secret=KEY))
        self.assertFalse(publish(provider(), enabled=True, client=None, secret=KEY))
        self.assertFalse(publish(ci(pr_head_sha="bad"), enabled=True,
                                 client=client, secret=KEY))
        self.assertEqual(len(client.calls), 1)

    def test_no_fake_positive_qa_and_duplicate_is_idempotent_in_summary(self):
        metrics = summarize([provider(), ci(), qa(), qa()])
        self.assertEqual(metrics["events"], 3)
        self.assertEqual(metrics["input_tokens"], 16)
        self.assertEqual(metrics["output_tokens"], 4)
        self.assertEqual(metrics["cost_micro_usd"], 120)
        self.assertEqual(metrics["ci_pass"], 1)
        self.assertEqual(metrics["qa_fail"], 1)
        self.assertEqual(metrics["ci_false_success"], 1)
        self.assertEqual(metrics["qa_pass"], 0)
        self.assertEqual(metrics["latency_ms"], 12+1200+80)

    def test_conflicting_sequence_reuse_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicting"):
            summarize([provider(), provider(cost_micro_usd=999)])

    def test_bounded_summary_and_rejected_untrusted_payload(self):
        with self.assertRaises(ValueError):
            summarize([{"prompt": "PRIVATE"}])
        events = (provider(sequence=i) for i in range(MAX_EVENTS+1))
        with self.assertRaisesRegex(ValueError, "budget"):
            summarize(events)

    def test_summary_is_deterministic_without_external_packages(self):
        one = summarize([ci(), provider(), qa()])
        two = summarize([qa(), ci(), provider()])
        self.assertEqual(one, two)


if __name__ == "__main__":
    unittest.main()
