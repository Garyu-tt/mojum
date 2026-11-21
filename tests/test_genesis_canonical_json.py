"""Acceptance QA: GENESIS provenance hashes must not describe lossy JSON."""

import unittest

from mojum.genesis import GenesisError, canonical, sha256


class CanonicalGenomeTests(unittest.TestCase):
    def test_reject_integer_keys_at_any_depth(self):
        for value in ({1: "value"}, {"outer": {1: "value"}},
                      {"nested": [{"ok": True}, {2: "value"}]}):
            with self.subTest(value=value), self.assertRaises(GenesisError):
                sha256(value)
        self.assertEqual(canonical({"1": "value"}), '{"1":"value"}')

    def test_reject_tuples_instead_of_silently_converting_to_arrays(self):
        for value in ((1, 2), {"acquired": (1, 2)},
                      {"nested": [{"child": (3, 4)}]}):
            with self.subTest(value=value), self.assertRaises(GenesisError):
                sha256(value)
        self.assertEqual(canonical({"acquired": [1, 2]}), '{"acquired":[1,2]}')

    def test_reject_invalid_json_and_utf8(self):
        for value in (float("nan"), float("inf"), object(),
                      {"name": "\\ud800"}):
            with self.subTest(type=type(value).__name__), self.assertRaises(GenesisError):
                sha256({"value": value})

    def test_normal_genome_and_list_still_hash_deterministically(self):
        value = {"schema": "mojum.genesis.genotype/1",
                 "inherited": {"heartbeat_bpm": 60},
                 "evidence": [{"tick": 1, "ok": True}, None]}
        self.assertEqual(sha256(value), sha256(value))
        self.assertEqual(canonical({"x": [1, 2, 3]}), '{"x":[1,2,3]}')


if __name__ == "__main__":
    unittest.main()
