"""GENESIS provenance hashes must reject lossy Python-to-JSON coercions."""

import unittest

from mojum.genesis import GenesisError, canonical, sha256


class CanonicalGenomeTests(unittest.TestCase):
    def test_rejects_integer_and_boolean_object_keys(self):
        for candidate in ({1: "value"}, {True: "value"}, {"nested": {3: "x"}}):
            with self.subTest(candidate=candidate), self.assertRaises(GenesisError):
                sha256(candidate)
        self.assertEqual(canonical({"1": "value"}), '{"1":"value"}')

    def test_rejects_tuple_to_array_coercion_at_any_depth(self):
        for candidate in ({"acquired": (1, 2)}, {"nested": [[1], ("x",)]}):
            with self.subTest(candidate=candidate), self.assertRaises(GenesisError):
                sha256(candidate)
        self.assertEqual(canonical({"acquired": [1, 2]}), '{"acquired":[1,2]}')

    def test_rejects_nonfinite_nonjson_and_invalid_utf8_values(self):
        for value in (float("nan"), float("inf"), object(), "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises(GenesisError):
                sha256({"value": value})

    def test_valid_genome_hash_and_wire_remain_stable(self):
        genome = {"schema": "mojum.genesis.genotype/1",
                  "inherited": {"heartbeat_bpm": 60, "body_scale_milli": 1000},
                  "provenance": ["synthetic", None, False]}
        self.assertEqual(sha256(genome), sha256(genome))
        self.assertEqual(canonical(genome), canonical({
            "provenance": ["synthetic", None, False],
            "inherited": {"body_scale_milli": 1000, "heartbeat_bpm": 60},
            "schema": "mojum.genesis.genotype/1",
        }))

    def test_rejects_unsupported_container(self):
        with self.assertRaises(GenesisError):
            sha256({"events": {1, 2}})


if __name__ == "__main__":
    unittest.main()
