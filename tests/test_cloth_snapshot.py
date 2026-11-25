"""Adversarial tests for the offline cloth snapshot/replay codec."""
import copy
import hashlib
import json
import unittest

from mojum.cloth_snapshot import (
    MAX_BYTES, MAX_REPLAY_BYTES, ClothSnapshotError, decode_snapshot,
    encode_snapshot, verify_replay,
)

MESH = hashlib.sha256(b"synthetic-mesh").hexdigest()


def state(tick=0):
    return {
        "tick": tick, "time_ns": tick * 10_000_000, "dt_ns": 10_000_000,
        "mesh_sha256": MESH, "rig_revision": "rig-r1",
        "profile_revision": "shirt-r1", "solver_id": "synthetic-proxy/1",
        "provenance": "PUBLIC_SYNTHETIC", "consent": True,
        "positions": [[0, 0, 0], [1, 0, 0]],
        "velocities": [[0, 0, 0], [0, 1, 0]],
    }


def forged_envelope(mutate):
    value = json.loads(encode_snapshot(state()))
    mutate(value["state"])
    payload = json.dumps(value["state"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    value["sha256"] = hashlib.sha256(payload.encode("ascii")).hexdigest()
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


class ClothSnapshotTests(unittest.TestCase):
    def test_round_trip_is_canonical_and_detached(self):
        source = state()
        encoded = encode_snapshot(source)
        decoded = decode_snapshot(encoded)
        self.assertEqual(decoded.positions, ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)))
        source["positions"][0][0] = 99
        self.assertEqual(decode_snapshot(encoded).positions[0][0], 0.0)
        self.assertEqual(encode_snapshot(state()), encoded)

    def test_valid_contiguous_replay_returns_ordered_digests(self):
        snapshots = [encode_snapshot(state(tick)) for tick in range(4)]
        self.assertEqual(len(verify_replay(snapshots)), 4)

    def test_replay_rejects_tick_gap_and_reordering(self):
        with self.assertRaisesRegex(ClothSnapshotError, "contiguous"):
            verify_replay([encode_snapshot(state(0)), encode_snapshot(state(2))])

    def test_replay_rejects_configuration_change(self):
        second = state(1)
        second["rig_revision"] = "rig-r2"
        with self.assertRaisesRegex(ClothSnapshotError, "configuration"):
            verify_replay([encode_snapshot(state()), encode_snapshot(second)])

    def test_replay_rejects_aggregate_byte_budget_before_decoding(self):
        # Each entry is within the per-snapshot cap, but their aggregate is not.
        oversized_replay = [b"x" * MAX_BYTES] * (MAX_REPLAY_BYTES // MAX_BYTES + 1)
        with self.assertRaisesRegex(ClothSnapshotError, "aggregate byte budget"):
            verify_replay(oversized_replay)

    def test_replay_rejects_non_native_entries_before_metaclass_or_length_callbacks(self):
        calls = []

        class PretendNative(type):
            def __eq__(cls, other):
                calls.append("metaclass.__eq__")
                return True

        class NonNativeEntry(metaclass=PretendNative):
            def __len__(self):
                calls.append("entry.__len__")
                return 1

        with self.assertRaises(ClothSnapshotError):
            verify_replay([NonNativeEntry()])
        self.assertEqual(calls, [])

        encoded = encode_snapshot(state())
        self.assertEqual(verify_replay([encoded]), verify_replay([encoded.decode("ascii")]))

    def test_rejects_oversized_text_before_encoding(self):
        with self.assertRaisesRegex(ClothSnapshotError, "byte size"):
            decode_snapshot("x" * (MAX_BYTES + 1))

    def test_rejects_boolean_tick_even_with_recomputed_digest(self):
        forged = forged_envelope(lambda s: s.update(tick=True))
        with self.assertRaisesRegex(ClothSnapshotError, "tick"):
            decode_snapshot(forged)

    def test_rejects_nonfinite_and_bool_components(self):
        for bad in (float("nan"), float("inf"), True):
            candidate = state()
            candidate["positions"][0][0] = bad
            with self.assertRaises(ClothSnapshotError):
                encode_snapshot(candidate)

    def test_rejects_non_native_components_before_metaclass_or_float_callbacks(self):
        calls = []

        class PretendNative(type):
            def __eq__(cls, other):
                calls.append("metaclass.__eq__")
                return True

        class NonNativeNumber(metaclass=PretendNative):
            def __float__(self):
                calls.append("number.__float__")
                return 0.5

        for field in ("positions", "velocities"):
            with self.subTest(field=field):
                calls.clear()
                candidate = state()
                candidate[field][0][0] = NonNativeNumber()
                with self.assertRaisesRegex(ClothSnapshotError, "native numbers"):
                    encode_snapshot(candidate)
                self.assertEqual(calls, [])

    def test_rejects_position_velocity_count_mismatch(self):
        candidate = state()
        candidate["velocities"].pop()
        with self.assertRaisesRegex(ClothSnapshotError, "count mismatch"):
            encode_snapshot(candidate)

    def test_rejects_timebase_mismatch(self):
        candidate = state(2)
        candidate["time_ns"] += 1
        with self.assertRaisesRegex(ClothSnapshotError, "tick"):
            encode_snapshot(candidate)

    def test_rejects_duplicate_json_keys(self):
        with self.assertRaisesRegex(ClothSnapshotError, "duplicate"):
            decode_snapshot(b'{"schema":"mojum.cloth-snapshot/1","schema":"x","state":{},"sha256":"' + b"0" * 64 + b'"}')

    def test_rejects_noncanonical_json(self):
        encoded = encode_snapshot(state())
        with self.assertRaisesRegex(ClothSnapshotError, "canonical"):
            decode_snapshot(b" " + encoded)

    def test_rejects_tampering(self):
        encoded = bytearray(encode_snapshot(state()))
        encoded[encoded.index(b'"tick":0') + len(b'"tick":')] = ord("1")
        with self.assertRaises(ClothSnapshotError):
            decode_snapshot(bytes(encoded))

    def test_rejects_unknown_fields_and_bad_mesh_digest(self):
        with self.assertRaisesRegex(ClothSnapshotError, "shape"):
            encode_snapshot({**state(), "extra": 1})
        candidate = state()
        candidate["mesh_sha256"] = "not-a-digest"
        with self.assertRaisesRegex(ClothSnapshotError, "SHA-256"):
            encode_snapshot(candidate)

    def test_rejects_oversized_and_wrong_type_inputs(self):
        with self.assertRaises(ClothSnapshotError):
            decode_snapshot(b"x" * (MAX_BYTES + 1))
        with self.assertRaises(ClothSnapshotError):
            decode_snapshot(bytearray(encode_snapshot(state())))
        with self.assertRaises(ClothSnapshotError):
            verify_replay([])

    def test_requires_explicit_public_synthetic_consent(self):
        candidate = state()
        candidate["consent"] = False
        with self.assertRaisesRegex(ClothSnapshotError, "consent"):
            encode_snapshot(candidate)
        candidate = state()
        candidate["provenance"] = "PRIVATE"
        with self.assertRaisesRegex(ClothSnapshotError, "provenance"):
            encode_snapshot(candidate)

    def test_rejects_hostile_string_subclasses_before_comparison_or_key_hashing(self):
        class HostileString(str):
            def __eq__(self, other):
                raise AssertionError("hostile equality executed")

            __hash__ = str.__hash__

        candidate = state()
        candidate["provenance"] = HostileString("PUBLIC_SYNTHETIC")
        with self.assertRaisesRegex(ClothSnapshotError, "provenance"):
            encode_snapshot(candidate)

        candidate = state()
        candidate[HostileString("unexpected")] = None
        with self.assertRaisesRegex(ClothSnapshotError, "shape"):
            encode_snapshot(candidate)

    def test_rejects_oversized_vertex_count(self):
        candidate = state()
        candidate["positions"] = [[0, 0, 0]] * 4097
        candidate["velocities"] = [[0, 0, 0]] * 4097
        with self.assertRaisesRegex(ClothSnapshotError, "vectors"):
            encode_snapshot(candidate)


if __name__ == "__main__":
    unittest.main()
