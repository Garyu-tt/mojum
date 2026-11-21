"""Pre-decode size budget for raw in-memory checkpoint envelopes.

Stacked QA on PR #44. Avoid decoding arbitrarily oversized input.
"""
import base64
import copy
import unittest
from unittest.mock import patch

from mojum.checkpoint_bundle import MAX_COMPONENT_BYTES, pack, unpack


class EncodedSizePreflightTests(unittest.TestCase):
    def setUp(self):
        self.parts = {name: b"x" for name in ("genome", "neural", "body", "world", "memory")}
        self.bundle = pack("dante-1", 1, self.parts)

    def test_encoded_length_rejected_before_decoder_invocation(self):
        oversized = 4 * ((MAX_COMPONENT_BYTES + 2) // 3) + 4
        malicious = copy.deepcopy(self.bundle)
        malicious["payloads"]["genome"] = "A" * oversized
        with patch("mojum.checkpoint_bundle.base64.b64decode",
                   side_effect=AssertionError("decoder must not be called")) as decoder:
            with self.assertRaisesRegex(ValueError, "oversized base64 component"):
                unpack(malicious)
            decoder.assert_not_called()

    def test_exact_component_limit_roundtrips(self):
        boundary = bytes(MAX_COMPONENT_BYTES)
        full = dict(self.parts, world=boundary)
        bundle = pack("dante-1", 1, full)
        self.assertEqual(unpack(bundle, expected_revision=1), full)
        self.assertEqual(len(base64.b64encode(boundary)), 4 * ((MAX_COMPONENT_BYTES + 2) // 3))

    def test_valid_small_component_still_works(self):
        self.assertEqual(unpack(self.bundle), self.parts)


if __name__ == "__main__":
    unittest.main()
