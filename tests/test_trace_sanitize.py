import unittest
from unittest.mock import Mock, patch

from mojum import trace_sanitize
from mojum.trace_sanitize import (
    SCHEMA,
    TraceSanitizationError,
    sanitize_trace_attributes,
    trace_attributes_digest,
)


class TraceSanitizeTests(unittest.TestCase):
    def test_valid_metadata_is_sorted_and_detached(self):
        source = {
            "pr_number": 697,
            "head_sha": "a" * 40,
            "event": "qa.completed",
            "fencing_epoch": 3,
            "duration_ms": 120,
        }
        clean = sanitize_trace_attributes(source)
        self.assertEqual(list(clean), sorted(source))
        self.assertEqual(clean["pr_number"], 697)
        source["pr_number"] = 1
        self.assertEqual(clean["pr_number"], 697)

    def test_empty_mapping_is_valid(self):
        self.assertEqual(sanitize_trace_attributes({}), {})

    def test_digest_is_stable_across_insertion_order(self):
        first = {"event": "qa.completed", "pr_number": 697}
        second = {"pr_number": 697, "event": "qa.completed"}
        self.assertEqual(trace_attributes_digest(first), trace_attributes_digest(second))
        self.assertEqual(len(trace_attributes_digest(first)), 64)
        self.assertEqual(SCHEMA, "mojum.trace-attributes/1")

    def test_rejects_unknown_and_sensitive_keys(self):
        for key in (
            "prompt", "source", "message", "token", "api_key",
            "host", "hostname", "device_id", "noosphere_event",
            "private_memory", "password",
        ):
            with self.subTest(key=key):
                with self.assertRaises(TraceSanitizationError):
                    sanitize_trace_attributes({key: "secret"})

    def test_rejects_nested_or_hostile_values(self):
        class HostileDict(dict):
            pass

        for value in (
            {"nested": "value"}, ["value"], b"bytes", object(),
            1.5, True, None,
        ):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(TraceSanitizationError):
                    sanitize_trace_attributes({"event": value})
        with self.assertRaises(TraceSanitizationError):
            sanitize_trace_attributes(HostileDict(event="qa.completed"))

    def test_rejects_invalid_identifiers_and_free_text(self):
        invalid = (
            {"trace_id": "not-a-trace-id"},
            {"lease_id": "worker-on-host-1"},
            {"head_sha": "short"},
            {"event": "user prompt contents"},
            {"outcome": "everything is fine"},
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(TraceSanitizationError):
                    sanitize_trace_attributes(value)

    def test_rejects_oversized_strings_before_regex(self):
        oversized = "a" * 1_000_000
        for field in trace_sanitize._STRING_PATTERNS:
            with self.subTest(field=field):
                matcher = Mock()
                with patch.dict(trace_sanitize._STRING_PATTERNS, {field: matcher}):
                    with self.assertRaises(TraceSanitizationError):
                        sanitize_trace_attributes({field: oversized})
                matcher.fullmatch.assert_not_called()

    def test_accepts_each_string_field_at_its_maximum_length(self):
        values = {
            "trace_id": "a" * 64,
            "lease_id": "b" * 64,
            "head_sha": "c" * 64,
            "event": "a" * 48,
            "component": "a" * 32,
            "operation": "a" * 48,
            "outcome": "deferred",
            "status": "incomplete",
            "reason_code": "a" * 48,
        }
        self.assertEqual(sanitize_trace_attributes(values), dict(sorted(values.items())))

    def test_rejects_bool_negative_and_oversized_counters(self):
        for value in (True, -1, 2**63):
            with self.subTest(value=value):
                with self.assertRaises(TraceSanitizationError):
                    sanitize_trace_attributes({"fencing_epoch": value})
        with self.assertRaises(TraceSanitizationError):
            sanitize_trace_attributes({"pr_number": 0})

    def test_rejects_non_string_keys_and_too_many_fields(self):
        with self.assertRaises(TraceSanitizationError):
            sanitize_trace_attributes({1: "bad"})
        with self.assertRaises(TraceSanitizationError):
            sanitize_trace_attributes({f"field_{i}": i for i in range(25)})

    def test_rejects_oversized_key_before_membership_lookup(self):
        class MembershipTrap:
            called = False

            def __iter__(self):
                return iter(trace_sanitize._STRING_PATTERNS)

            def __contains__(self, value):
                self.called = True
                raise AssertionError("oversized key reached membership lookup")

        trap = MembershipTrap()
        with patch.object(trace_sanitize, "_ALLOWED_FIELDS", trap):
            with self.assertRaises(TraceSanitizationError):
                sanitize_trace_attributes({"x" * 1_000_000: 1})
        self.assertFalse(trap.called)

    def test_rejects_hostile_string_subclass_key_before_hashing(self):
        class HostileKey(str):
            active = False

            def __hash__(self):
                if self.active:
                    raise AssertionError("hostile hash must not run")
                return str.__hash__(self)

            def __eq__(self, other):
                if self.active:
                    raise AssertionError("hostile equality must not run")
                return str.__eq__(self, other)

        key = HostileKey("event")
        attributes = {key: "qa.completed"}
        key.active = True
        with self.assertRaises(TraceSanitizationError):
            sanitize_trace_attributes(attributes)

    def test_digest_revalidates_input(self):
        with self.assertRaises(TraceSanitizationError):
            trace_attributes_digest({"prompt": "do not export"})


if __name__ == "__main__":
    unittest.main()
