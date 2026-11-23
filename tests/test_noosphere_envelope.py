"""Hermetic tests for the optional Noosphere AEAD envelope."""
from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import patch

from mojum.noosphere_envelope import NoosphereEnvelopeError, open_bytes, seal_bytes

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
except ImportError:
    AESGCM = None


@unittest.skipIf(AESGCM is None, "optional cryptography dependency is not installed")
class EnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.key = bytes(range(32))
        self.context = {
            "key_id": "fixture-key-v1",
            "agent_id": "dante-fixture",
            "visibility": "PRIVATE",
            "content_type": "application/vnd.mojum.snapshot+json",
        }

    def seal(self, data=b'{"tick":7}'):
        return seal_bytes(data, key=self.key, **self.context)

    def open(self, data, **overrides):
        expected = {
            "expected_key_id": self.context["key_id"],
            "expected_agent_id": self.context["agent_id"],
            "expected_visibility": self.context["visibility"],
            "expected_content_type": self.context["content_type"],
        }
        expected.update(overrides)
        return open_bytes(data, key=self.key, **expected)

    def test_round_trip_and_no_plaintext_in_envelope(self):
        original = b'{"tick":7,"memory":"synthetic"}'
        sealed = self.seal(original)
        self.assertEqual(self.open(sealed), original)
        self.assertNotIn(original, sealed)
        self.assertIn(b"mojum.noosphere.encrypted-envelope/1", sealed)

    def test_maximum_one_mib_plaintext_round_trips(self):
        # AES-GCM adds a 16-byte tag and JSON base64 adds approximately 4/3
        # expansion. The advertised plaintext maximum must still be usable.
        original = b"x" * 1_048_576
        sealed = self.seal(original)
        self.assertLessEqual(len(sealed), 4 * ((len(original) + 16 + 2) // 3) + 8192)
        self.assertEqual(self.open(sealed), original)

    def test_random_nonce_changes_envelope(self):
        with patch("mojum.noosphere_envelope.secrets.token_bytes",
                   side_effect=[b"1" * 12, b"2" * 12]):
            first = self.seal(b"same")
            second = self.seal(b"same")
        self.assertNotEqual(first, second)
        self.assertEqual(self.open(first), b"same")
        self.assertEqual(self.open(second), b"same")

    def test_tampered_ciphertext_rejected(self):
        obj = json.loads(self.seal())
        raw = bytearray(base64.b64decode(obj["ciphertext"]))
        raw[0] ^= 1
        obj["ciphertext"] = base64.b64encode(raw).decode("ascii")
        tampered = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
        with self.assertRaises(NoosphereEnvelopeError):
            self.open(tampered)

    def test_wrong_key_rejected(self):
        sealed = self.seal()
        with self.assertRaises(NoosphereEnvelopeError):
            open_bytes(
                sealed, key=b"x" * 32,
                expected_key_id=self.context["key_id"],
                expected_agent_id=self.context["agent_id"],
                expected_visibility=self.context["visibility"],
                expected_content_type=self.context["content_type"],
            )

    def test_each_context_dimension_is_bound(self):
        sealed = self.seal()
        for field, value in (
            ("expected_key_id", "other-key"),
            ("expected_agent_id", "other-agent"),
            ("expected_visibility", "PUBLIC"),
            ("expected_content_type", "application/octet-stream"),
        ):
            with self.subTest(field=field), self.assertRaises(NoosphereEnvelopeError):
                self.open(sealed, **{field: value})

    def test_metadata_tamper_rejected_even_with_valid_json(self):
        obj = json.loads(self.seal())
        obj["agent_id"] = "other-agent"
        tampered = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
        with self.assertRaises(NoosphereEnvelopeError):
            self.open(tampered)

    def test_noncanonical_and_duplicate_json_rejected(self):
        sealed = self.seal()
        with self.assertRaises(NoosphereEnvelopeError):
            self.open(sealed + b" ")
        duplicate = b'{"schema":"x","schema":"y"}'
        with self.assertRaises(NoosphereEnvelopeError):
            self.open(duplicate)

    def test_base64_and_length_checks(self):
        obj = json.loads(self.seal())
        obj["nonce"] = "not base64!"
        bad = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
        with self.assertRaises(NoosphereEnvelopeError):
            self.open(bad)
        obj = json.loads(self.seal())
        obj["nonce"] = base64.b64encode(b"short").decode()
        bad = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
        with self.assertRaises(NoosphereEnvelopeError):
            self.open(bad)

    def test_empty_plaintext_is_supported(self):
        self.assertEqual(self.open(self.seal(b"")), b"")

    def test_visibility_and_content_type_are_validated(self):
        with self.assertRaises(NoosphereEnvelopeError):
            seal_bytes(b"x", key=self.key, **{**self.context, "visibility": "unknown"})
        with self.assertRaises(NoosphereEnvelopeError):
            seal_bytes(b"x", key=self.key, **{**self.context, "content_type": ""})

    def test_input_type_and_budget_guards(self):
        with self.assertRaises(NoosphereEnvelopeError):
            seal_bytes(bytearray(b"x"), key=self.key, **self.context)
        with self.assertRaises(NoosphereEnvelopeError):
            seal_bytes(b"x" * (1_048_576 + 1), key=self.key, **self.context)
        with self.assertRaises(NoosphereEnvelopeError):
            seal_bytes(b"x", key=b"short", **self.context)
        with self.assertRaises(NoosphereEnvelopeError):
            self.open(bytearray(self.seal()))

    def test_key_never_appears_in_wire(self):
        sealed = self.seal()
        self.assertNotIn(self.key, sealed)
        self.assertNotIn(base64.b64encode(self.key), sealed)

    def test_wrong_schema_algorithm_and_extra_fields_rejected(self):
        for field, value in (("schema", "other/9"), ("algorithm", "AES-128-GCM"),
                             ("unexpected", "value")):
            obj = json.loads(self.seal())
            obj[field] = value
            bad = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
            with self.subTest(field=field), self.assertRaises(NoosphereEnvelopeError):
                self.open(bad)


if __name__ == "__main__":
    unittest.main()
