"""Self-contained, offline regressions for the detached world-state codec."""
import json
import unittest

from mojum.world_room import Room, RoomState, advance, initial_state
from mojum.world_state_snapshot import (
    MAX_SNAPSHOT_BYTES, SCHEMA, WorldSnapshotError,
    decode_snapshot, encode_snapshot,
)


class WorldStateSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(5, 4, ((2, 1, .125, 2),))
        self.state = initial_state(self.room, 1, 2)

    def test_initial_state_roundtrip(self):
        room, state = decode_snapshot(encode_snapshot(self.room, self.state))
        self.assertEqual(room, self.room)
        self.assertEqual(state, self.state)

    def test_after_multiple_ticks_and_future_motion(self):
        state = self.state
        for tick, delta in enumerate((.2, .4, 0., -.1), 1):
            state, _ = advance(self.room, state, tick=tick, dx=delta, dy=0,
                               expected_revision=state.revision)
        restored_room, restored_state = decode_snapshot(encode_snapshot(self.room, state))
        self.assertEqual(
            advance(self.room, state, tick=5, dx=.1, dy=0,
                    expected_revision=state.revision),
            advance(restored_room, restored_state, tick=5, dx=.1, dy=0,
                    expected_revision=restored_state.revision),
        )

    def test_stable_bytes_and_primitive_numeric_types(self):
        data = encode_snapshot(self.room, self.state)
        self.assertEqual(data, encode_snapshot(self.room, self.state))
        self.assertTrue(data.startswith(b'{"fingerprint":'))
        self.assertEqual(json.loads(data)["schema"], SCHEMA)
        self.assertEqual(json.loads(data)["room"]["width"], 5)
        self.assertEqual(encode_snapshot(*decode_snapshot(data)), data)

    def test_detached_room_is_independent(self):
        room, state = decode_snapshot(encode_snapshot(self.room, self.state))
        self.assertIsNot(room, self.room)
        self.assertIsNot(state, self.state)
        self.assertEqual(room.obstacles, self.room.obstacles)

    def test_invalid_revision_cannot_be_encoded(self):
        fake = RoomState(0, 1, 2, "0" * 64)
        with self.assertRaises(WorldSnapshotError):
            encode_snapshot(self.room, fake)

    def test_invalid_position_cannot_be_encoded(self):
        fake = RoomState(0, 2, 2, self.state.revision)
        with self.assertRaises(WorldSnapshotError):
            encode_snapshot(self.room, fake)

    def test_mutated_room_rechecked(self):
        room = Room(5, 4)
        object.__setattr__(room, "obstacles", ((4.9, 0, 1, 1),))
        with self.assertRaises(WorldSnapshotError):
            encode_snapshot(room, initial_state(self.room, 1, 2))

    def test_bool_and_nan_rejected_before_revision_hash(self):
        for value in (True, float("nan"), float("inf"), 1_000_001):
            with self.subTest(value=value), self.assertRaises(WorldSnapshotError):
                encode_snapshot(Room(5, 4), RoomState(0, value, 2, self.state.revision))

    def test_invalid_ticks(self):
        for tick in (True, -1, 1_000_000_001, 1.0):
            with self.subTest(tick=tick), self.assertRaises(WorldSnapshotError):
                encode_snapshot(self.room, RoomState(tick, 1, 2, self.state.revision))

    def test_constructor_bypass_is_not_accepted(self):
        fake = object.__new__(RoomState)
        with self.assertRaises(WorldSnapshotError):
            encode_snapshot(self.room, fake)

    def test_corrupt_body_or_fingerprint_is_detected(self):
        wire = encode_snapshot(self.room, self.state)
        for broken in (wire.replace(b'"width":5', b'"width":6'),
                       wire.replace(b'"fingerprint":"', b'"fingerprint":"a')):
            with self.subTest(broken=broken[:90]), self.assertRaises(WorldSnapshotError):
                decode_snapshot(broken)

    def test_modified_state_with_original_fingerprint_rejected(self):
        wire = encode_snapshot(self.room, self.state)
        altered = wire.replace(b'"tick":0', b'"tick":1')
        self.assertNotEqual(altered, wire)
        with self.assertRaises(WorldSnapshotError):
            decode_snapshot(altered)

    def test_whitespace_and_noncanonical_numeric_token(self):
        wire = encode_snapshot(self.room, self.state)
        for broken in (wire + b" ", wire.replace(b'"width":5', b'"width":5.0')):
            with self.subTest(broken=broken[:90]), self.assertRaises(WorldSnapshotError):
                decode_snapshot(broken)

    def test_reject_duplicate_json_keys(self):
        wire = encode_snapshot(self.room, self.state)
        with self.assertRaisesRegex(WorldSnapshotError, "duplicate"):
            decode_snapshot(wire.replace(b'"width":5', b'"width":5,"width":5'))

    def test_reject_large_or_invalid_json(self):
        for wire in (b"{", b"\xff", b"[]", b"true", b"NaN", b"" * 0,
                     b"0" * (MAX_SNAPSHOT_BYTES + 1),
                     b'{"tick":' + b"9" * 5000 + b"}"):
            with self.subTest(size=len(wire)), self.assertRaises(WorldSnapshotError):
                decode_snapshot(wire)

    def test_no_unknown_fields_or_versions(self):
        wire = encode_snapshot(self.room, self.state)
        record = json.loads(wire)
        for update in ({"schema": "mojum.world.point-room-state/2"},
                       {"extra": 1}):
            changed = dict(record)
            changed.update(update)
            with self.assertRaises(WorldSnapshotError):
                decode_snapshot(json.dumps(changed).encode())

    def test_reject_excess_obstacles(self):
        room = Room(5, 4, ((2, 1, .125, 2),))
        object.__setattr__(room, "obstacles", room.obstacles * 4097)
        with self.assertRaises(WorldSnapshotError):
            encode_snapshot(room, self.state)

    def test_negative_zero_survives_canonical_roundtrip(self):
        room = Room(3., 4.)
        state = initial_state(room, -0.0, 2.)
        wire = encode_snapshot(room, state)
        self.assertEqual(encode_snapshot(*decode_snapshot(wire)), wire)


if __name__ == "__main__":
    unittest.main()
