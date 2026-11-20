import copy
import unittest

from mojum.checkpoint_manifest import make_manifest, verify_manifest


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.parts = {name: name.encode() for name in ("genome", "neural", "body", "world", "memory")}
        self.manifest = make_manifest("dante-1", 3, self.parts)

    def test_roundtrip(self):
        self.assertTrue(verify_manifest(self.manifest, self.parts, expected_revision=3))

    def test_deterministic(self):
        self.assertEqual(self.manifest, make_manifest("dante-1", 3, dict(reversed(list(self.parts.items())))))

    def test_corruption(self):
        with self.assertRaises(ValueError):
            verify_manifest(self.manifest, dict(self.parts, body=b"tampered"))

    def test_stale_revision(self):
        with self.assertRaises(ValueError):
            verify_manifest(self.manifest, self.parts, expected_revision=4)

    def test_missing_component(self):
        with self.assertRaises(ValueError):
            make_manifest("dante-1", 3, {"body": b"x"})

    def test_reject_bool_revision(self):
        with self.assertRaises(ValueError):
            make_manifest("dante-1", True, self.parts)

    def test_manifest_tamper(self):
        broken = copy.deepcopy(self.manifest)
        broken["organism_id"] = "other"
        with self.assertRaises(ValueError):
            verify_manifest(broken, self.parts)

    def test_unsupported_schema(self):
        with self.assertRaises(ValueError):
            verify_manifest(dict(self.manifest, schema="other"), self.parts)

    def test_extra_field(self):
        with self.assertRaises(ValueError):
            verify_manifest(dict(self.manifest, extra=1), self.parts)


if __name__ == "__main__":
    unittest.main()
