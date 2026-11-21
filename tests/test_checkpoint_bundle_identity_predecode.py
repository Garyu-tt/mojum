"""Caller-pinned checkpoint identity must fail before heavy component decoding."""
import copy
import unittest
from unittest.mock import patch

from mojum.checkpoint_bundle import pack, unpack, dumps, loads


class CheckpointIdentityPredecodeTests(unittest.TestCase):
    def setUp(self):
        parts = {name: name.encode("ascii") for name in
                 ("genome", "neural", "body", "world", "memory")}
        self.parts = parts
        self.bundle = pack("dante-1", 7, parts)

    def test_wrong_organism_and_revision_skip_all_component_decoding(self):
        attempts = (
            {"expected_organism_id": "other-organism"},
            {"expected_organism_id": True},
            {"expected_revision": 8},
            {"expected_revision": True},
        )
        for kwargs in attempts:
            with self.subTest(kwargs=kwargs):
                with patch("mojum.checkpoint_bundle._decode",
                           side_effect=AssertionError("decoder reached")) as decode:
                    with self.assertRaises(ValueError):
                        unpack(self.bundle, **kwargs)
                    decode.assert_not_called()

    def test_malformed_manifest_identity_fails_before_decoding(self):
        for field, bad in (("revision", True), ("revision", "7"),
                           ("organism_id", []), ("organism_id", None)):
            with self.subTest(field=field, bad=bad):
                malformed = copy.deepcopy(self.bundle)
                malformed["manifest"][field] = bad
                kwargs = ({"expected_revision": 7} if field == "revision"
                          else {"expected_organism_id": "dante-1"})
                with patch("mojum.checkpoint_bundle._decode",
                           side_effect=AssertionError("decoder reached")) as decode:
                    with self.assertRaises(ValueError):
                        unpack(malformed, **kwargs)
                    decode.assert_not_called()

    def test_matching_identity_still_requires_digest_validation(self):
        self.assertEqual(
            unpack(self.bundle, expected_revision=7,
                   expected_organism_id="dante-1"), self.parts)
        wire = dumps(self.bundle)
        self.assertEqual(
            loads(wire, expected_revision=7,
                  expected_organism_id="dante-1"), self.bundle)
        forged = copy.deepcopy(self.bundle)
        forged["payloads"]["body"] = "dGFtcGVyZWQ="
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            unpack(forged, expected_revision=7,
                   expected_organism_id="dante-1")

    def test_loads_verifies_five_components_exactly_once(self):
        import mojum.checkpoint_bundle as module
        wire = dumps(self.bundle)
        with patch.object(module, "_decode", wraps=module._decode) as decode:
            self.assertEqual(
                loads(wire, expected_revision=7,
                      expected_organism_id="dante-1"), self.bundle)
            self.assertEqual(decode.call_count, 5)
        # A redundant validation pass must not be required for strict JSON.
        with self.assertRaisesRegex(ValueError, "noncanonical"):
            loads(wire + " ")

    def test_missing_manifest_with_expected_identity_fails_cleanly(self):
        malformed = dict(self.bundle, manifest=None)
        with patch("mojum.checkpoint_bundle._decode",
                   side_effect=AssertionError("decoder reached")) as decode:
            with self.assertRaises(ValueError):
                unpack(malformed, expected_organism_id="dante-1")
            decode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
