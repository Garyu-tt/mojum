import unittest

from mojum.noosphere_key_lifecycle import (
    KeyLifecycleError, admit_key_operation, validate_keyring,
)


def key(**changes):
    value = {
        "key_id": "export-2026-a", "purpose": "noosphere-export",
        "algorithm": "AES-256-GCM", "created_at": 10, "activated_at": 20,
        "retired_at": None, "revoked_at": None, "material_available": True,
    }
    value.update(changes)
    return value


class KeyLifecycleTests(unittest.TestCase):
    def test_canonical_order_and_stable_digest(self):
        a, b = key(), key(key_id="export-2026-b")
        one = validate_keyring([a, b], now=30)
        two = validate_keyring([b, a], now=30)
        self.assertEqual(one["digest"], two["digest"])
        self.assertEqual([dict(x)["key_id"] for x in one["keys"]],
                         ["export-2026-a", "export-2026-b"])

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(KeyLifecycleError):
            validate_keyring([key(), key()], now=30)

    def test_unknown_or_missing_fields_rejected(self):
        bad = key(extra="no")
        with self.assertRaises(KeyLifecycleError):
            validate_keyring([bad], now=30)
        bad = key()
        del bad["algorithm"]
        with self.assertRaises(KeyLifecycleError):
            validate_keyring([bad], now=30)

    def test_bool_is_not_timestamp(self):
        with self.assertRaises(KeyLifecycleError):
            validate_keyring([key(created_at=True)], now=30)

    def test_activation_must_follow_creation_and_not_be_future(self):
        with self.assertRaises(KeyLifecycleError):
            validate_keyring([key(activated_at=9)], now=30)
        with self.assertRaises(KeyLifecycleError):
            validate_keyring([key(activated_at=31)], now=30)

    def test_retirement_and_revocation_chronology(self):
        with self.assertRaises(KeyLifecycleError):
            validate_keyring([key(retired_at=19)], now=30)
        with self.assertRaises(KeyLifecycleError):
            validate_keyring([key(revoked_at=31)], now=30)

    def test_encrypt_denied_before_activation(self):
        self.assertEqual(admit_key_operation(key(), operation="encrypt", at=19),
                         {"allowed": False, "reason": "not_yet_active"})

    def test_retired_key_decrypts_but_cannot_encrypt(self):
        record = key(retired_at=25)
        self.assertFalse(admit_key_operation(record, operation="encrypt", at=25)["allowed"])
        self.assertTrue(admit_key_operation(record, operation="decrypt", at=25)["allowed"])

    def test_revoked_key_denied_for_both_operations(self):
        record = key(revoked_at=25)
        for operation in ("encrypt", "decrypt"):
            self.assertEqual(admit_key_operation(record, operation=operation, at=25),
                             {"allowed": False, "reason": "revoked"})

    def test_unavailable_material_denied(self):
        self.assertEqual(admit_key_operation(key(material_available=False),
                                             operation="decrypt", at=21),
                         {"allowed": False, "reason": "key_material_unavailable"})

    def test_invalid_operation_rejected(self):
        with self.assertRaises(KeyLifecycleError):
            admit_key_operation(key(), operation="rotate", at=21)


    def test_hostile_string_subclass_is_rejected_before_comparison(self):
        class Hostile(str):
            def __eq__(self, other):
                raise AssertionError("comparison must not run")
            __hash__ = str.__hash__
        bad = key()
        bad["purpose"] = Hostile("noosphere-export")
        with self.assertRaises(KeyLifecycleError):
            validate_keyring([bad], now=30)

    def test_hostile_operation_object_is_rejected_by_exact_type(self):
        class Hostile:
            def __eq__(self, other):
                raise AssertionError("comparison must not run")
        with self.assertRaises(KeyLifecycleError):
            admit_key_operation(key(), operation=Hostile(), at=21)

    def test_bounded_keyring(self):
        with self.assertRaises(KeyLifecycleError):
            validate_keyring([], now=30)
        with self.assertRaises(KeyLifecycleError):
            validate_keyring([key(key_id=f"k-{i}") for i in range(129)], now=30)

    def test_timestamp_bounds_apply_to_both_public_apis(self):
        limit = (1 << 63) - 1
        huge = 1 << 10000
        with self.assertRaisesRegex(KeyLifecycleError, "signed 64-bit"):
            validate_keyring([key()], now=huge)
        with self.assertRaisesRegex(KeyLifecycleError, "signed 64-bit"):
            admit_key_operation(key(), operation="encrypt", at=huge)
        for field in ("created_at", "activated_at", "retired_at", "revoked_at"):
            with self.subTest(field=field):
                with self.assertRaises(KeyLifecycleError):
                    validate_keyring([key(**{field: huge})], now=limit)
                with self.assertRaises(KeyLifecycleError):
                    admit_key_operation(key(**{field: huge}), operation="decrypt", at=limit)
        self.assertEqual(validate_keyring([key(created_at=limit, activated_at=limit)],
                                          now=limit)["schema"],
                         "mojum.noosphere-key-lifecycle/1")
        self.assertTrue(admit_key_operation(
            key(created_at=limit, activated_at=limit),
            operation="decrypt", at=limit)["allowed"])


if __name__ == "__main__":
    unittest.main()
