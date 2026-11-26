import unittest
from unittest.mock import patch

from mojum import archive_key_rotation as rotation
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
            __hash__ = str.__hash__

            def __eq__(self, other):
                raise AssertionError("hostile equality must not run")

        fingerprint = HostileString(key_fingerprint(self.k1))
        keyring = {fingerprint: self.k1}
        with self.assertRaises(RotationError):
            verify_rotation_chain(self.k0, 0, [], keyring, key_fingerprint(self.k0))

    def test_record_rejects_string_subclass_key_before_set(self):
        class HostileString(str):
            __hash__ = str.__hash__
            active = False

            def __eq__(self, other):
                if self.active:
                    raise AssertionError("hostile equality must not run")
                return super().__eq__(other)

        r = make_rotation(1, self.k0, self.k1)
        hostile_key = HostileString("schema")
        r[hostile_key] = r.pop("schema")
        hostile_key.active = True
        with self.assertRaises(RotationError):
            verify_rotation_chain(self.k0, 0, [r], {}, key_fingerprint(self.k1))

    def test_oversized_record_key_rejected_before_hashing(self):
        record = make_rotation(1, self.k0, self.k1)
        record["x" * 1_000_000] = None
        original_set = set

        def bounded_set(values):
            values = tuple(values)
            self.assertTrue(
                all(type(value) is str and len(value) <= 32 for value in values),
                "oversized record key reached hashing",
            )
            return original_set(values)

        with patch("mojum.archive_key_rotation.set", side_effect=bounded_set, create=True):
            with self.assertRaises(RotationError):
                verify_rotation_chain(
                    self.k0, 0, [record], {}, key_fingerprint(self.k1)
                )

    def test_oversized_fingerprints_and_signature_rejected_before_regex(self):
        valid = make_rotation(1, self.k0, self.k1)
        oversized = "a" * 1_000_000

        cases = [
            lambda: verify_rotation_chain(self.k0, 0, [], {}, oversized),
            lambda: verify_rotation_chain(self.k0, 0, [], {oversized: self.k1}, key_fingerprint(self.k0)),
        ]
        for field in ("previous_fingerprint", "next_fingerprint", "signature"):
            record = dict(valid)
            record[field] = oversized
            cases.append(lambda record=record: verify_rotation_chain(
                self.k0, 0, [record], {}, key_fingerprint(self.k1)
            ))

        for index, invoke in enumerate(cases):
            with self.subTest(case=index):
                with patch.object(rotation, "_HEX_256") as pattern:
                    with self.assertRaises(RotationError):
                        invoke()
                    scanned = [call.args[0] for call in pattern.fullmatch.call_args_list]
                    self.assertNotIn(oversized, scanned)
                    self.assertTrue(all(type(value) is str and len(value) == 64 for value in scanned))

    def test_oversized_schema_rejected_before_comparison(self):
        record = make_rotation(1, self.k0, self.k1)
        record["schema"] = "x" * 1_000_000
        with self.assertRaisesRegex(RotationError, "schema"):
            verify_rotation_chain(self.k0, 0, [record], {}, key_fingerprint(self.k1))


if __name__ == "__main__":
    unittest.main()
