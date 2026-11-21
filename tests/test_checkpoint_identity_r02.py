"""Opt-in identity binding of portable checkpoint envelopes.

A valid hash and revision are not a proof the checkpoint belongs to the
organism being restored. This is an integrity/namespace gate, not a
signature, encryption, credential or trusted origin.
"""
import unittest
from mojum.checkpoint_bundle import pack, unpack, dumps, loads


PARTS = {n: n.encode("ascii") for n in (
    "genome", "neural", "body", "world", "memory",
)}


class BundleOrganismBindingTests(unittest.TestCase):
    def test_same_revision_different_organism_fails_explicit_restore_gate(self):
        valid_a = pack("dante-a", 17, PARTS)
        valid_b = pack("dante-b", 17, PARTS)
        # This previously succeeded despite naming a different organism.
        self.assertEqual(unpack(valid_b, expected_revision=17), PARTS)
        self.assertEqual(unpack(valid_a, expected_revision=17,
                                expected_organism_id="dante-a"), PARTS)
        with self.assertRaisesRegex(ValueError, "organism identity mismatch"):
            unpack(valid_b, expected_revision=17,
                   expected_organism_id="dante-a")

    def test_canonical_wire_decoder_passes_identity_gate(self):
        a = dumps(pack("dante-a", 3, PARTS))
        b = dumps(pack("dante-b", 3, PARTS))
        self.assertEqual(
            unpack(loads(a, expected_revision=3,
                         expected_organism_id="dante-a")),
            PARTS,
        )
        with self.assertRaisesRegex(ValueError, "organism identity mismatch"):
            loads(b, expected_revision=3, expected_organism_id="dante-a")

    def test_wrong_type_identity_never_silently_matches(self):
        bundle = pack("dante-a", 3, PARTS)
        wire = dumps(bundle)
        for value in (False, True, 3, [], {}, 3.0, ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    unpack(bundle, expected_organism_id=value)
                with self.assertRaises(ValueError):
                    loads(wire, expected_organism_id=value)

    def test_legacy_default_behavior_is_explicitly_unchanged(self):
        bundle = pack("dante-a", 3, PARTS)
        self.assertEqual(unpack(bundle), PARTS)
        self.assertEqual(unpack(loads(dumps(bundle))), PARTS)

    def test_stale_revision_and_identity_independently_rejected(self):
        bundle = pack("dante-a", 3, PARTS)
        with self.assertRaises(ValueError):
            unpack(bundle, expected_revision=2, expected_organism_id="dante-a")
        with self.assertRaises(ValueError):
            unpack(bundle, expected_revision=3, expected_organism_id="dante-b")


if __name__ == "__main__":
    unittest.main()
