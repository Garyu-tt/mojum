"""GENESIS hashes must not alias JSON-coerced noncanonical inputs."""
import unittest

from mojum.genesis import GenesisError, canonical, sha256


class CanonicalGenomeTests(unittest.TestCase):
    def test_rejects_integer_object_key_collision(self):
        with self.assertRaises(GenesisError):
            sha256({1: "value"})
        self.assertEqual(canonical({"1": "value"}), '{"1":"value"}')

    def test_rejects_tuple_to_array_coercion(self):
        with self.assertRaises(GenesisError):
            sha256({"acquired": (1, 2)})
        self.assertEqual(canonical({"acquired": [1, 2]}), '{"acquired":[1,2]}')

    def test_rejects_nonfinite_and_nonjson_types(self):
        for value in (float("nan"), float("inf"), object()):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(GenesisError):
                    sha256({"value": value})

    def test_normal_genome_hash_is_stable(self):
        value = {"schema": "mojum.genesis.genotype/1", "inherited": {"heartbeat_bpm": 60}}
        self.assertEqual(sha256(value), sha256(value))


if __name__ == "__main__":
    unittest.main()
