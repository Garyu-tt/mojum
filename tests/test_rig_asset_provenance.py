"""Hermetic adversarial checks for a claimed synthetic virtual rig bundle."""
from __future__ import annotations

import copy
import hashlib
import unittest

from mojum.rig_asset_provenance import (
    DOF_COUNT, JOINT_COUNT, MAX_ASSET_BYTES, RigProvenanceError,
    SCHEMA, verify_rig_assets,
)


def fixture():
    # 32 two-DOF + 21 one-DOF joints = exactly 85 DOF.
    joints = [{
        "id": f"j{i:02d}", "parent": f"j{i - 1:02d}" if i else None,
        "dof": 2 if i < 32 else 1,
    } for i in range(JOINT_COUNT)]
    raw = b"<mujoco model='synthetic'/>"
    manifest = {
        "schema": SCHEMA,
        "classification": "synthetic_unverified",
        "source": {
            "repository": "example/virtual-rig",
            "revision": "1" * 40,
            "license_spdx": "MIT",
        },
        "joints": joints,
        "assets": [{
            "path": "assets/body.mjcf", "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }],
        "calibration_evidence_asset": None,
    }
    return manifest, {"assets/body.mjcf": raw}


class RigAssetProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.manifest, self.contents = fixture()

    def reject(self):
        with self.assertRaises(RigProvenanceError):
            verify_rig_assets(self.manifest, self.contents)

    def test_valid_rig_and_deterministic_digest(self):
        first = verify_rig_assets(self.manifest, self.contents)
        self.assertEqual((first["joint_count"], first["dof_count"]), (53, 85))
        self.assertEqual(first["authorization"], "none_integrity_only")
        # JSON key order is not part of the source identity.
        swapped = dict(reversed(list(self.manifest.items())))
        self.assertEqual(first, verify_rig_assets(swapped, self.contents))

    def test_bad_joint_count(self):
        self.manifest["joints"].pop()
        self.reject()

    def test_duplicate_joint(self):
        self.manifest["joints"][1]["id"] = "j00"
        self.reject()

    def test_missing_or_future_parent(self):
        self.manifest["joints"][1]["parent"] = "j30"
        self.reject()

    def test_more_than_one_root(self):
        self.manifest["joints"][1]["parent"] = None
        self.reject()

    def test_wrong_dof_sum(self):
        self.manifest["joints"][0]["dof"] = 1
        self.reject()

    def test_boolean_dof_not_integer(self):
        self.manifest["joints"][0]["dof"] = True
        self.reject()

    def test_extra_manifest_key(self):
        self.manifest["verified_calibration"] = True
        self.reject()

    def test_source_revision_must_be_pinned(self):
        self.manifest["source"]["revision"] = "main"
        self.reject()

    def test_source_repo_traversal(self):
        self.manifest["source"]["repository"] = "../virtual-rig"
        self.reject()

    def test_asset_path_traversal(self):
        self.manifest["assets"][0]["path"] = "assets/../../private"
        self.reject()

    def test_duplicate_assets(self):
        self.manifest["assets"].append(copy.deepcopy(self.manifest["assets"][0]))
        self.reject()

    def test_missing_or_extra_asset(self):
        self.contents.clear()
        self.reject()
        self.contents["assets/body.mjcf"] = b"<mujoco model='synthetic'/>"
        self.contents["assets/unclaimed"] = b"secret"
        self.reject()

    def test_tampered_asset(self):
        self.contents["assets/body.mjcf"] = b"<mujoco model='tampered'/>"
        self.reject()

    def test_fake_digest(self):
        self.manifest["assets"][0]["sha256"] = "0" * 64
        self.reject()

    def test_reject_mutable_binary_and_boolean_size(self):
        self.contents["assets/body.mjcf"] = bytearray(self.contents["assets/body.mjcf"])
        self.reject()
        self.manifest["assets"][0]["bytes"] = True
        self.reject()

    def test_asset_budget_rejected_without_allocating_large_file(self):
        self.manifest["assets"][0]["bytes"] = MAX_ASSET_BYTES + 1
        self.reject()

    def test_claimed_calibration_requires_existing_evidence(self):
        self.manifest["classification"] = "calibration_claim_unverified"
        self.reject()
        self.manifest["calibration_evidence_asset"] = "assets/body.mjcf"
        receipt = verify_rig_assets(self.manifest, self.contents)
        self.assertEqual(receipt["classification"], "calibration_claim_unverified")
        self.assertEqual(receipt["authorization"], "none_integrity_only")

    def test_synthetic_cannot_assert_calibration_evidence(self):
        self.manifest["calibration_evidence_asset"] = "assets/body.mjcf"
        self.reject()

    def test_false_numeric_and_unknown_versions(self):
        self.manifest["schema"] = "mojum.virtual-rig-provenance/2"
        self.reject()


    def test_hostile_string_subclass_dict_keys_fail_closed(self):
        # An exact dict can still contain a str subclass with hostile __eq__.
        # Key primitives must be checked before constructing/comparing sets.
        class HostileKey(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                raise AssertionError("untrusted key comparison")

        for location in ("manifest", "source", "joint", "asset", "contents"):
            with self.subTest(location=location):
                manifest, contents = fixture()
                if location == "manifest":
                    target, original = manifest, "schema"
                elif location == "source":
                    target, original = manifest["source"], "repository"
                elif location == "joint":
                    target, original = manifest["joints"][0], "id"
                elif location == "asset":
                    target, original = manifest["assets"][0], "path"
                else:
                    target, original = contents, "assets/body.mjcf"
                value = target.pop(original)
                target[HostileKey(original)] = value
                with self.assertRaises(RigProvenanceError):
                    verify_rig_assets(manifest, contents)

if __name__ == "__main__":
    unittest.main()
