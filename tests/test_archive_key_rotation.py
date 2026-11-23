import unittest

from mojum.archive_key_rotation import (
    RotationError, key_fingerprint, make_rotation, verify_rotation_chain,
)


class RotationChainTests(unittest.TestCase):
    def setUp(self):
        self.k0 = b"a" * 32
        self.k1 = b"b" * 32
        self.k2 = b"c" * 32

    def test_empty_chain_is_pinned_to_initial_key(self):
        report = verify_rotation_chain(self.k0, 7, [], {}, key_fingerprint(self.k0))
        self.assertEqual((report.final_epoch, report.rotation_count), (7, 0))

    def test_two_step_chain(self):
        r1 = make_rotation(8, self.k0, self.k1)
        r2 = make_rotation(9, self.k1, self.k2)
        report = verify_rotation_chain(
            self.k0, 7, [r1, r2],
            {key_fingerprint(self.k1): self.k1},
            key_fingerprint(self.k2),
        )
        self.assertEqual(report.final_epoch, 9)
        self.assertEqual(report.rotation_count, 2)
        self.assertNotIn(self.k0.hex(), repr(report))

    def test_wrong_terminal_anchor_rejected(self):
        r = make_rotation(1, self.k0, self.k1)
        with self.assertRaises(RotationError):
            verify_rotation_chain(self.k0, 0, [r], {}, key_fingerprint(self.k2))

    def test_tampered_signature_rejected(self):
        r = make_rotation(1, self.k0, self.k1)
        r["signature"] = "0" * 64
        with self.assertRaises(RotationError):
            verify_rotation_chain(self.k0, 0, [r], {}, key_fingerprint(self.k1))

    def test_gap_and_replay_epoch_rejected(self):
        r = make_rotation(2, self.k0, self.k1)
        with self.assertRaises(RotationError):
            verify_rotation_chain(self.k0, 0, [r], {}, key_fingerprint(self.k1))

    def test_cycle_rejected(self):
        r1 = make_rotation(1, self.k0, self.k1)
        r2 = make_rotation(2, self.k1, self.k0)
        with self.assertRaises(RotationError):
            verify_rotation_chain(
                self.k0, 0, [r1, r2],
                {key_fingerprint(self.k1): self.k1}, key_fingerprint(self.k0),
            )

    def test_intermediate_key_must_be_present(self):
        r1 = make_rotation(1, self.k0, self.k1)
        r2 = make_rotation(2, self.k1, self.k2)
        with self.assertRaises(RotationError):
            verify_rotation_chain(self.k0, 0, [r1, r2], {}, key_fingerprint(self.k2))

    def test_duplicate_or_extra_fields_rejected(self):
        r = make_rotation(1, self.k0, self.k1)
        r["extra"] = "ignored?"
        with self.assertRaises(RotationError):
            verify_rotation_chain(self.k0, 0, [r], {}, key_fingerprint(self.k1))

    def test_non_native_integer_and_boolean_rejected(self):
        with self.assertRaises(RotationError):
            make_rotation(True, self.k0, self.k1)
        r = make_rotation(1, self.k0, self.k1)
        r["epoch"] = True
        with self.assertRaises(RotationError):
            verify_rotation_chain(self.k0, 0, [r], {}, key_fingerprint(self.k1))

    def test_bounds_and_key_types(self):
        with self.assertRaises(RotationError):
            key_fingerprint(bytearray(b"x" * 32))
        with self.assertRaises(RotationError):
            verify_rotation_chain(self.k0, 0, [make_rotation(i + 1, self.k0, self.k1) for i in range(33)], {}, key_fingerprint(self.k1))

    def test_intermediate_key_fingerprint_mismatch_rejected(self):
        r1 = make_rotation(1, self.k0, self.k1)
        r2 = make_rotation(2, self.k1, self.k2)
        with self.assertRaises(RotationError):
            verify_rotation_chain(
                self.k0, 0, [r1, r2],
                {key_fingerprint(self.k1): self.k2}, key_fingerprint(self.k2),
            )

    def test_oversized_record_rejected_before_key_materialization(self):
        r = make_rotation(1, self.k0, self.k1)
        r.update({f"extra-{i}": None for i in range(10_000)})
        with self.assertRaises(RotationError):
            verify_rotation_chain(self.k0, 0, [r], {}, key_fingerprint(self.k1))

    def test_keyring_cardinality_is_bounded(self):
        keyring = {f"{i:064x}": self.k1 for i in range(33)}
        with self.assertRaises(RotationError):
            verify_rotation_chain(self.k0, 0, [], keyring, key_fingerprint(self.k0))

    def test_keyring_rejects_string_subclass_before_lookup(self):
        class HostileString(str):
            def __eq__(self, other):
                raise AssertionError("hostile equality must not run")

        fingerprint = HostileString(key_fingerprint(self.k1))
        keyring = {fingerprint: self.k1}
        with self.assertRaises(RotationError):
            verify_rotation_chain(self.k0, 0, [], keyring, key_fingerprint(self.k0))

    def test_record_rejects_string_subclass_key_before_set(self):
        class HostileString(str):
            def __eq__(self, other):
                raise AssertionError("hostile equality must not run")

        r = make_rotation(1, self.k0, self.k1)
        r[HostileString("schema")] = r.pop("schema")
        with self.assertRaises(RotationError):
            verify_rotation_chain(self.k0, 0, [r], {}, key_fingerprint(self.k1))


if __name__ == "__main__":
    unittest.main()
