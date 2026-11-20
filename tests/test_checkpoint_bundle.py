import copy
import unittest

from mojum.checkpoint_bundle import pack, unpack, dumps, loads, MAX_COMPONENT_BYTES


class CheckpointBundleTests(unittest.TestCase):
    def setUp(self):
        self.parts = {name: name.encode() for name in ("genome", "neural", "body", "world", "memory")}
        self.bundle = pack("dante-1", 7, self.parts)

    def test_roundtrip_and_determinism(self):
        self.assertEqual(unpack(loads(dumps(self.bundle), expected_revision=7)), self.parts)
        self.assertEqual(dumps(self.bundle), dumps(pack("dante-1", 7, dict(reversed(list(self.parts.items()))))))

    def test_reject_corrupted_component(self):
        bad = copy.deepcopy(self.bundle)
        bad["payloads"]["body"] = "dGFtcGVyZWQ="
        with self.assertRaises(ValueError):
            unpack(bad)

    def test_reject_malformed_base64(self):
        for bad_encoding in ("!!!", "YQ", "YQ==\n", 123):
            bad = copy.deepcopy(self.bundle)
            bad["payloads"]["world"] = bad_encoding
            with self.subTest(bad_encoding=bad_encoding), self.assertRaises(ValueError):
                unpack(bad)

    def test_reject_stale_revision(self):
        with self.assertRaises(ValueError):
            unpack(self.bundle, expected_revision=8)

    def test_reject_extra_or_missing_component(self):
        for mutation in (lambda d: d.pop("body"), lambda d: d.update(extra="")):
            bad = copy.deepcopy(self.bundle)
            mutation(bad["payloads"])
            with self.assertRaises(ValueError):
                unpack(bad)

    def test_reject_duplicate_keys_and_noncanonical_wire(self):
        wire = dumps(self.bundle)
        with self.assertRaises(ValueError):
            loads(wire.replace('"schema":', '"schema":"shadow","schema":', 1))
        with self.assertRaises(ValueError):
            loads(wire + " ")

    def test_size_budget(self):
        with self.assertRaises(ValueError):
            pack("dante-1", 7, dict(self.parts, body=b"x" * (MAX_COMPONENT_BYTES + 1)))

    def test_reject_nonbyte_and_bool_revision(self):
        with self.assertRaises(ValueError):
            pack("dante-1", 7, dict(self.parts, body="text"))
        with self.assertRaises(ValueError):
            pack("dante-1", True, self.parts)


if __name__ == "__main__":
    unittest.main()
