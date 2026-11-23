"""Privacy boundary tests for offline operational trace metadata."""

import math
import unittest

from mojum.trace_privacy import (
    SCHEMA,
    OfflineTraceSink,
    TracePrivacyError,
    sanitize_trace_event,
)

BASE = {
    "schema": SCHEMA,
    "trace_id": "a" * 32,
    "lease_id": "lease-17",
    "fencing_token": 3,
    "pr_number": 697,
    "head_sha": "b" * 40,
    "event_type": "tests.completed",
    "timestamp_ms": 123456,
}


class TracePrivacyTests(unittest.TestCase):
    def test_accepts_metadata_and_returns_independent_json_copy(self):
        raw = {**BASE, "latency_ms": 14, "input_tokens": 120, "cost_usd": 0.002}
        envelope = sanitize_trace_event(raw)
        first = envelope.as_dict()
        first["lease_id"] = "mutated"
        self.assertEqual(envelope.lease_id, "lease-17")
        self.assertEqual(envelope.as_dict()["lease_id"], "lease-17")
        self.assertEqual(envelope.as_dict()["cost_usd"], 0.002)

    def test_optional_null_fields_are_omitted(self):
        raw = {**BASE, "latency_ms": None, "cost_usd": None}
        self.assertNotIn("latency_ms", sanitize_trace_event(raw).as_dict())

    def test_rejects_unknown_sensitive_payload_without_echoing_key(self):
        raw = {**BASE, "prompt_text": "private prompt"}
        with self.assertRaises(TracePrivacyError) as caught:
            sanitize_trace_event(raw)
        self.assertNotIn("prompt_text", str(caught.exception))
        self.assertNotIn("private prompt", str(caught.exception))

    def test_rejects_nested_payloads(self):
        for key, value in (
            ("labels", {"team": "private"}),
            ("source", ["secret"]),
            ("metadata", {"token": "secret"}),
        ):
            with self.subTest(key=key), self.assertRaises(TracePrivacyError):
                sanitize_trace_event({**BASE, key: value})

    def test_rejects_non_plain_mapping(self):
        class MappingLike(dict):
            pass

        with self.assertRaises(TracePrivacyError):
            sanitize_trace_event(MappingLike(BASE))

    def test_rejects_invalid_schema_and_event(self):
        for patch in ({"schema": "mojum.trace-envelope.v2"}, {"event_type": "prompt.logged"}):
            with self.subTest(patch=patch), self.assertRaises(TracePrivacyError):
                sanitize_trace_event({**BASE, **patch})

    def test_rejects_invalid_identifiers_and_sha(self):
        for patch in (
            {"trace_id": "not-a-trace"},
            {"lease_id": "../secret"},
            {"head_sha": "A" * 40},
            {"head_sha": "f" * 39},
        ):
            with self.subTest(patch=patch), self.assertRaises(TracePrivacyError):
                sanitize_trace_event({**BASE, **patch})

    def test_rejects_bool_as_integer(self):
        for key in ("fencing_token", "pr_number", "timestamp_ms"):
            with self.subTest(key=key), self.assertRaises(TracePrivacyError):
                sanitize_trace_event({**BASE, key: True})

    def test_rejects_negative_or_wrong_optional_metrics(self):
        for patch in (
            {"latency_ms": -1},
            {"input_tokens": True},
            {"output_tokens": -2},
            {"cost_usd": -0.1},
            {"cost_usd": math.nan},
            {"cost_usd": math.inf},
            {"cost_usd": "0.1"},
        ):
            with self.subTest(patch=patch), self.assertRaises(TracePrivacyError):
                sanitize_trace_event({**BASE, **patch})

    def test_rejects_oversized_schema_and_event_values_before_lookup(self):
        huge = "x" * 1_000_000
        with self.assertRaises(TracePrivacyError):
            sanitize_trace_event({**BASE, "schema": huge})

        class ExplodingAllowlist:
            def __contains__(self, value):
                raise AssertionError("oversized event reached allowlist lookup")

        from unittest.mock import patch
        with patch("mojum.trace_privacy._ALLOWED_EVENTS", ExplodingAllowlist()):
            with self.assertRaises(TracePrivacyError):
                sanitize_trace_event({**BASE, "event_type": huge})

    def test_rejects_oversized_and_non_string_keys(self):
        with self.assertRaises(TracePrivacyError):
            sanitize_trace_event({**BASE, "extra": 1})
        with self.assertRaises(TracePrivacyError):
            sanitize_trace_event({**BASE, 4: "unexpected"})

    def test_rejects_oversized_key_before_hashing(self):
        from unittest.mock import patch

        raw = {**BASE, "x" * 100_000: None}
        with patch("mojum.trace_privacy.set",
                   side_effect=AssertionError("oversized key reached hashing"),
                   create=True):
            with self.assertRaisesRegex(TracePrivacyError, "bounded native strings"):
                sanitize_trace_event(raw)

    def test_rejects_hostile_schema_string_subclass_before_comparison(self):
        class HostileString(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                raise AssertionError("hostile schema equality executed")

        raw = {**BASE, "schema": HostileString(SCHEMA)}
        with self.assertRaises(TracePrivacyError):
            sanitize_trace_event(raw)

    def test_rejects_missing_required_fields(self):
        for key in BASE:
            with self.subTest(key=key), self.assertRaises(TracePrivacyError):
                raw = dict(BASE)
                del raw[key]
                sanitize_trace_event(raw)

    def test_rejects_cost_integer_that_overflows_float_conversion(self):
        with self.assertRaises(TracePrivacyError):
            sanitize_trace_event({**BASE, "cost_usd": 10**10000})

    def test_rejects_oversized_integer_metadata(self):
        huge = 10**10000
        for field in ("fencing_token", "pr_number", "timestamp_ms",
                      "latency_ms", "input_tokens", "output_tokens"):
            with self.subTest(field=field), self.assertRaises(TracePrivacyError):
                sanitize_trace_event({**BASE, field: huge})

    def test_oversized_key_rejected_before_hashing(self):
        from unittest.mock import patch

        huge_key = "x" * 1_000_000
        raw = {**BASE, huge_key: None}
        with patch("mojum.trace_privacy.set", side_effect=AssertionError("oversized key reached hashing"), create=True):
            with self.assertRaisesRegex(TracePrivacyError, "bounded native strings"):
                sanitize_trace_event(raw)

    def test_offline_sink_validates_but_never_persists_or_sends(self):
        sink = OfflineTraceSink()
        self.assertIs(sink.emit(BASE), False)
        with self.assertRaises(TracePrivacyError):
            sink.emit({**BASE, "prompt": "private"})
