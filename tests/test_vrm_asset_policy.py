import copy
import hashlib
import unittest

from mojum.vrm_asset_policy import AssetPolicyError, SCHEMA, validate_manifest


POLICY = {
    "allowed_dependencies": ["three", "three-vrm"],
    "allowed_licenses": ["CC0-1.0", "MIT"],
    "allowed_media_types": ["model/gltf-binary"],
    "max_asset_bytes": 8_000_000,
    "max_dependencies": 8,
}


def manifest():
    return {
        "schema": SCHEMA,
        "provenance": "PUBLIC_SYNTHETIC",
        "asset": {
            "path": "avatars/dante.vrm",
            "sha256": hashlib.sha256(b"synthetic").hexdigest(),
            "size_bytes": 9,
            "media_type": "model/gltf-binary",
            "source": "local-import",
            "license": "CC0-1.0",
        },
        "dependencies": [
            {"name": "three", "version": "0.170.0", "license": "MIT"},
            {"name": "three-vrm", "version": "3.2.0", "license": "MIT"},
        ],
    }


class HostileDict(dict):
    def __iter__(self):
        raise AssertionError("hostile mapping must not be iterated")


class VrmAssetPolicyTests(unittest.TestCase):
    def test_valid_local_pinned_manifest(self):
        report = validate_manifest(manifest(), POLICY)
        self.assertTrue(report["accepted"])
        self.assertEqual(report["violations"], [])
        self.assertEqual(len(report["manifest_digest"]), 64)
        self.assertEqual(report["normalized"]["asset"]["path"], "avatars/dante.vrm")

    def test_deterministic_digest_independent_of_dependency_order(self):
        first = manifest()
        second = manifest()
        second["dependencies"].reverse()
        a = validate_manifest(first, POLICY)
        b = validate_manifest(second, POLICY)
        self.assertEqual(a["manifest_digest"], b["manifest_digest"])
        self.assertEqual(a["normalized"], b["normalized"])

    def test_input_is_not_mutated_and_output_is_detached(self):
        original = manifest()
        before = copy.deepcopy(original)
        report = validate_manifest(original, POLICY)
        self.assertEqual(original, before)
        report["normalized"]["asset"]["path"] = "tampered"
        self.assertEqual(original["asset"]["path"], "avatars/dante.vrm")

    def test_reject_remote_path_traversal_and_absolute_path(self):
        for path in ("../avatar.vrm", "/tmp/avatar.vrm", "https://example.invalid/a.vrm",
                     "avatars/../../secret.vrm", "C:/avatar.vrm"):
            candidate = manifest()
            candidate["asset"]["path"] = path
            self.assertIn("asset.path", validate_manifest(candidate, POLICY)["violations"])

    def test_reject_missing_hash_unapproved_license_and_media_type(self):
        candidate = manifest()
        candidate["asset"].update(sha256="", license="Proprietary", media_type="application/octet-stream")
        violations = validate_manifest(candidate, POLICY)["violations"]
        self.assertIn("asset.sha256", violations)
        self.assertIn("asset.license", violations)
        self.assertIn("asset.media_type", violations)

    def test_reject_unpinned_or_unapproved_dependencies(self):
        candidate = manifest()
        candidate["dependencies"].append({"name": "unknown-lib", "version": "latest", "license": "MIT"})
        violations = validate_manifest(candidate, POLICY)["violations"]
        self.assertIn("dependencies[2].unapproved_name", violations)
        self.assertIn("dependencies[2].version", violations)

    def test_reject_duplicate_dependency_names(self):
        candidate = manifest()
        candidate["dependencies"].append(dict(candidate["dependencies"][0]))
        self.assertTrue(any(v.endswith(".duplicate") for v in validate_manifest(candidate, POLICY)["violations"]))

    def test_bool_is_not_a_valid_size_or_policy_limit(self):
        candidate = manifest()
        candidate["asset"]["size_bytes"] = True
        self.assertIn("asset.size_bytes", validate_manifest(candidate, POLICY)["violations"])
        bad_policy = dict(POLICY, max_asset_bytes=True)
        with self.assertRaises(AssetPolicyError):
            validate_manifest(manifest(), bad_policy)

    def test_reject_oversized_asset_and_dependency_list(self):
        candidate = manifest()
        candidate["asset"]["size_bytes"] = POLICY["max_asset_bytes"] + 1
        self.assertIn("asset.size_bytes", validate_manifest(candidate, POLICY)["violations"])
        bad_policy = dict(POLICY, max_dependencies=1)
        with self.assertRaises(AssetPolicyError):
            validate_manifest(manifest(), bad_policy)

    def test_exact_container_types_required(self):
        with self.assertRaises(AssetPolicyError):
            validate_manifest(HostileDict(manifest()), POLICY)
        with self.assertRaises(AssetPolicyError):
            validate_manifest(manifest(), HostileDict(POLICY))

    def test_fail_closed_on_wrong_provenance_and_schema(self):
        candidate = manifest()
        candidate["provenance"] = "PRIVATE"
        candidate["schema"] = "future/2"
        violations = validate_manifest(candidate, POLICY)["violations"]
        self.assertEqual(violations, ["provenance", "schema"])


if __name__ == "__main__":
    unittest.main()
