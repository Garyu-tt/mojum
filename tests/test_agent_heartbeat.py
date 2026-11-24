"""Adversarial tests for the offline external-agent heartbeat contract."""

import json
import unittest

from mojum.agent_heartbeat import (
    HeartbeatError,
    SCHEMA,
    sign_heartbeat,
    verify_heartbeat,
)

KEY = b"k" * 32
NOW = 1_000_000_000_000


def envelope(**overrides):
    values = {
        "agent_id": "worker-01",
        "run_id": "run-abc",
        "sequence": 7,
        "event": "heartbeat",
        "issued_at_ns": NOW - 1_000_000_000,
        "expires_at_ns": NOW + 10_000_000_000,
        "key": KEY,
    }
    values.update(overrides)
    return sign_heartbeat(**values)


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


class HeartbeatAdmissionTests(unittest.TestCase):
    def test_valid_envelope_returns_sanitized_receipt(self):
        value = envelope()
        result = verify_heartbeat(value, key=KEY, now_ns=NOW)
        self.assertEqual(result.agent_id, "worker-01")
        self.assertEqual(result.run_id, "run-abc")
        self.assertEqual(result.sequence, 7)
        self.assertEqual(result.event, "heartbeat")
        self.assertEqual(len(result.payload_sha256), 64)
        self.assertFalse(hasattr(result, "signature"))
        self.assertFalse(hasattr(result, "key"))

    def test_canonical_json_round_trip(self):
        value = envelope()
        self.assertEqual(verify_heartbeat(wire(value), key=KEY, now_ns=NOW),
                         verify_heartbeat(value, key=KEY, now_ns=NOW))

    def test_accepts_canonical_utf8_bytes(self):
        value = envelope()
        self.assertEqual(
            verify_heartbeat(wire(value).encode("utf-8"), key=KEY, now_ns=NOW),
            verify_heartbeat(value, key=KEY, now_ns=NOW),
        )

    def test_rejects_noncanonical_wire(self):
        value = envelope()
        with self.assertRaisesRegex(HeartbeatError, "canonical"):
            verify_heartbeat(json.dumps(value), key=KEY, now_ns=NOW)

    def test_rejects_duplicate_json_keys(self):
        value = envelope()
        encoded = wire(value)
        encoded = encoded[:-1] + ',"sequence":7}'
        with self.assertRaisesRegex(HeartbeatError, "duplicate"):
            verify_heartbeat(encoded, key=KEY, now_ns=NOW)

    def test_rejects_unknown_and_missing_fields(self):
        value = envelope()
        with self.assertRaises(HeartbeatError):
            verify_heartbeat({**value, "extra": 1}, key=KEY, now_ns=NOW)
        with self.assertRaises(HeartbeatError):
            verify_heartbeat({k: v for k, v in value.items() if k != "event"},
                             key=KEY, now_ns=NOW)

    def test_rejects_oversized_mapping_before_key_snapshot(self):
        value = envelope()
        value.update({f"extra-{index}": index for index in range(25_000)})
        with self.assertRaisesRegex(HeartbeatError, "missing or unknown fields"):
            verify_heartbeat(value, key=KEY, now_ns=NOW)

    def test_rejects_bool_as_sequence_and_clock(self):
        value = envelope()
        value["sequence"] = True
        with self.assertRaises(HeartbeatError):
            verify_heartbeat(value, key=KEY, now_ns=NOW)
        with self.assertRaises(HeartbeatError):
            verify_heartbeat(envelope(), key=KEY, now_ns=True)

    def test_rejects_expired_and_future_events(self):
        with self.assertRaisesRegex(HeartbeatError, "expired"):
            verify_heartbeat(envelope(issued_at_ns=NOW - 20_000_000_000,
                                      expires_at_ns=NOW - 1_000_000_000),
                             key=KEY, now_ns=NOW)
        with self.assertRaisesRegex(HeartbeatError, "not yet valid"):
            verify_heartbeat(envelope(issued_at_ns=NOW + 10_000_000_000,
                                      expires_at_ns=NOW + 20_000_000_000),
                             key=KEY, now_ns=NOW)

    def test_rejects_excessive_lifetime(self):
        value = envelope(expires_at_ns=NOW + 120_000_000_000)
        with self.assertRaisesRegex(HeartbeatError, "lifetime"):
            verify_heartbeat(value, key=KEY, now_ns=NOW)

    def test_rejects_wrong_key_and_tampering(self):
        value = envelope()
        with self.assertRaisesRegex(HeartbeatError, "authentication"):
            verify_heartbeat(value, key=b"x" * 32, now_ns=NOW)
        value["sequence"] = 8
        with self.assertRaisesRegex(HeartbeatError, "authentication"):
            verify_heartbeat(value, key=KEY, now_ns=NOW)

    def test_rejects_bad_key_sizes_and_events(self):
        with self.assertRaises(HeartbeatError):
            sign_heartbeat(agent_id="a", run_id="r", sequence=1,
                           event="heartbeat", issued_at_ns=1,
                           expires_at_ns=2, key=b"short")
        with self.assertRaises(HeartbeatError):
            envelope(event="restart")

    def test_rejects_hostile_mapping_keys_before_hashing_or_comparison(self):
        class HostileKey(str):
            calls = 0

            def __hash__(self):
                type(self).calls += 1
                return super().__hash__()

        value = envelope()
        signature = value.pop("signature")
        value[HostileKey("signature")] = signature
        calls_after_insert = HostileKey.calls
        with self.assertRaisesRegex(HeartbeatError, "native strings"):
            verify_heartbeat(value, key=KEY, now_ns=NOW)
        self.assertEqual(HostileKey.calls, calls_after_insert)

    def test_rejects_integer_literals_over_python_parser_limit(self):
        raw = b'{"sequence":' + b"9" * 5000 + b"}"
        with self.assertRaisesRegex(HeartbeatError, "invalid JSON"):
            verify_heartbeat(raw, key=KEY, now_ns=NOW)

    def test_schema_is_versioned(self):
        self.assertEqual(SCHEMA, "mojum.agent-heartbeat/1")

    def test_receipt_is_detached_from_input(self):
        value = envelope()
        result = verify_heartbeat(value, key=KEY, now_ns=NOW)
        value["agent_id"] = "changed"
        self.assertEqual(result.agent_id, "worker-01")

    def test_rejects_oversized_wire_before_decode_or_encode(self):
        with self.assertRaisesRegex(HeartbeatError, "size"):
            verify_heartbeat(b"x" * 16_385, key=KEY, now_ns=NOW)
        with self.assertRaisesRegex(HeartbeatError, "size"):
            verify_heartbeat("x" * 16_385, key=KEY, now_ns=NOW)

    def test_rejects_utf8_expansion_over_byte_budget(self):
        with self.assertRaisesRegex(HeartbeatError, "size"):
            verify_heartbeat("é" * 9_000, key=KEY, now_ns=NOW)


if __name__ == "__main__":
    unittest.main()
