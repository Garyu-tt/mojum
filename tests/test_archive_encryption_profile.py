import dataclasses
import unittest

from mojum.archive_encryption_profile import (
    ArchiveEncryptionProfileError,
    admit_profile,
    verify_profile,
)


def fixture():
    return {
        "schema": "mojum.archive-encryption-profile/1",
        "classification": "PRIVATE",
        "enabled": True,
        "cipher": "AES-256-GCM",
        "key_ref": "owner-archive-key",
        "key_version": 1,
        "nonce_bytes": 12,
        "tag_bytes": 16,
        "segment_id": "segment-0001",
        "plaintext_sha256": "a" * 64,
        "ciphertext_sha256": "b" * 64,
        "aad_schema": "mojum.archive-aad/1",
    }


class HostileEquality:
    def __eq__(self, other):
        raise AssertionError("untrusted equality must not execute")


class ArchiveEncryptionProfileTests(unittest.TestCase):
    def test_accepts_and_verifies_a_valid_private_profile(self):
        profile = admit_profile(fixture(), private_opt_in=True)
        self.assertTrue(verify_profile(profile))
        self.assertEqual(profile.cipher, "AES-256-GCM")
        self.assertEqual(len(profile.digest), 64)

    def test_accepts_second_reviewed_aead_suite(self):
        raw = fixture()
        raw["cipher"] = "ChaCha20-Poly1305"
        profile = admit_profile(raw, private_opt_in=True)
        self.assertTrue(verify_profile(profile))

    def test_requires_explicit_private_opt_in(self):
        with self.assertRaises(ArchiveEncryptionProfileError):
            admit_profile(fixture())

    def test_rejects_public_or_restricted_classification(self):
        for classification in ("PUBLIC", "RESTRICTED", "PRIVATE "):
            raw = fixture()
            raw["classification"] = classification
            with self.subTest(classification=classification):
                with self.assertRaises(ArchiveEncryptionProfileError):
                    admit_profile(raw, private_opt_in=True)

    def test_rejects_unknown_cipher_and_wrong_nonce_or_tag(self):
        for field, value in (
            ("cipher", "AES-128-CBC"),
            ("nonce_bytes", 16),
            ("tag_bytes", 12),
        ):
            raw = fixture()
            raw[field] = value
            with self.subTest(field=field):
                with self.assertRaises(ArchiveEncryptionProfileError):
                    admit_profile(raw, private_opt_in=True)

    def test_rejects_bool_as_integer_and_integer_subclasses(self):
        class IntLike(int):
            pass
        for field, value in (
            ("key_version", True),
            ("key_version", IntLike(1)),
            ("nonce_bytes", True),
            ("tag_bytes", IntLike(16)),
        ):
            raw = fixture()
            raw[field] = value
            with self.subTest(field=field, value=repr(value)):
                with self.assertRaises(ArchiveEncryptionProfileError):
                    admit_profile(raw, private_opt_in=True)

    def test_rejects_unknown_missing_and_extra_fields(self):
        raw = fixture()
        del raw["key_ref"]
        with self.assertRaises(ArchiveEncryptionProfileError):
            admit_profile(raw, private_opt_in=True)
        raw = fixture()
        raw["key_material"] = "not-allowed"
        with self.assertRaises(ArchiveEncryptionProfileError):
            admit_profile(raw, private_opt_in=True)

    def test_rejects_paths_and_key_material_as_key_reference(self):
        for key_ref in ("/tmp/archive.key", "../key", "https://host/key", ""):
            raw = fixture()
            raw["key_ref"] = key_ref
            with self.subTest(key_ref=key_ref):
                with self.assertRaises(ArchiveEncryptionProfileError):
                    admit_profile(raw, private_opt_in=True)

    def test_rejects_bad_hashes_and_unsupported_aad(self):
        for field, value in (
            ("plaintext_sha256", "A" * 64),
            ("ciphertext_sha256", "f" * 63),
            ("aad_schema", "mojum.archive-aad/2"),
        ):
            raw = fixture()
            raw[field] = value
            with self.subTest(field=field):
                with self.assertRaises(ArchiveEncryptionProfileError):
                    admit_profile(raw, private_opt_in=True)

    def test_rejects_custom_mapping_and_hostile_values_without_comparing(self):
        class DictLike(dict):
            pass
        with self.assertRaises(ArchiveEncryptionProfileError):
            admit_profile(DictLike(fixture()), private_opt_in=True)
        raw = fixture()
        raw["schema"] = HostileEquality()
        with self.assertRaises(ArchiveEncryptionProfileError):
            admit_profile(raw, private_opt_in=True)

    def test_rejects_non_string_and_subclassed_keys(self):
        class Key(str):
            pass
        raw = fixture()
        raw[Key("extra")] = "x"
        with self.assertRaises(ArchiveEncryptionProfileError):
            admit_profile(raw, private_opt_in=True)

    def test_profile_is_detached_and_digest_detects_tampering(self):
        raw = fixture()
        profile = admit_profile(raw, private_opt_in=True)
        raw["key_ref"] = "mutated-after-admission"
        self.assertEqual(profile.key_ref, "owner-archive-key")
        self.assertTrue(verify_profile(profile))
        changed = dataclasses.replace(profile, key_version=2)
        self.assertFalse(verify_profile(changed))

    def test_rejects_wrong_profile_object(self):
        with self.assertRaises(ArchiveEncryptionProfileError):
            verify_profile({"digest": "a" * 64})

    def test_rejects_oversized_text_before_regex_or_digest(self):
        for field in ("schema", "classification", "cipher", "key_ref",
                      "segment_id", "plaintext_sha256", "ciphertext_sha256",
                      "aad_schema"):
            raw = fixture()
            raw[field] = "x" * 1_000_000
            with self.subTest(field=field):
                with self.assertRaisesRegex(ArchiveEncryptionProfileError, "length budget"):
                    admit_profile(raw, private_opt_in=True)

    def test_verify_rejects_oversized_digest_before_regex(self):
        profile = admit_profile(fixture(), private_opt_in=True)
        changed = dataclasses.replace(profile, digest="x" * 1_000_000)
        with self.assertRaises(ArchiveEncryptionProfileError):
            verify_profile(changed)

    def test_rejects_oversized_profile_key_before_hashing(self):
        raw = fixture()
        raw["x" * 1_000_000] = None
        # The oversized key must be rejected before schema-set hashing.
        with self.assertRaisesRegex(ArchiveEncryptionProfileError, "at most 32"):
            admit_profile(raw, private_opt_in=True)


if __name__ == "__main__":
    unittest.main()
