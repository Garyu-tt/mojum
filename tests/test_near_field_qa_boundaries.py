"""QA regression: strict direct Python ray-state and snapshot boundaries."""
import unittest

from mojum.developmental_policy import DevelopmentError
from mojum.near_field import MODEL_SCHEMA, RangeExplorer, sense_room_rays
from mojum.world_room import Room, RoomState


class _SpoofedSchema:
    def __eq__(self, other):
        return True


class _ExplodingSchema:
    def __eq__(self, other):
        raise RuntimeError("untrusted equality callback must not run")


class _StringSubclass(str):
    pass


class NearFieldQaBoundaries(unittest.TestCase):
    def test_huge_forged_position_rejected_as_development_error(self):
        room = Room(4, 4)
        for x in (10 ** 1000, -(10 ** 1000)):
            with self.subTest(x_sign=x > 0), self.assertRaises(DevelopmentError):
                sense_room_rays(room, RoomState(0, x, 1, "a" * 64))

    def test_boolean_and_subclass_position_rejected(self):
        room = Room(4, 4)
        for x in (True, False, 1.0):
            # The first two values are forged bool, not spatial numbers.
            if type(x) is bool:
                with self.subTest(x=x), self.assertRaises(DevelopmentError):
                    sense_room_rays(room, RoomState(0, x, 1, "a" * 64))
            else:
                self.assertEqual(len(sense_room_rays(
                    room, RoomState(0, x, 1, "a" * 64))), 4)

    def test_snapshot_schema_equality_must_not_run(self):
        for forged in (_SpoofedSchema(), _ExplodingSchema()):
            snapshot = RangeExplorer().snapshot()
            snapshot["schema"] = forged
            with self.subTest(kind=type(forged).__name__):
                with self.assertRaises(DevelopmentError):
                    RangeExplorer.from_snapshot(snapshot)

    def test_snapshot_rejects_non_native_top_level_keys(self):
        snapshot = RangeExplorer().snapshot()
        snapshot[_StringSubclass("schema")] = snapshot.pop("schema")
        with self.assertRaises(DevelopmentError):
            RangeExplorer.from_snapshot(snapshot)

    def test_snapshot_rejects_non_native_action_keys(self):
        snapshot = {
            "schema": MODEL_SCHEMA, "last_tick": 0, "updates": 1,
            "records": {"2:4|0,0,0,0": {_StringSubclass("x+"): [1, 0]}},
        }
        with self.assertRaises(DevelopmentError):
            RangeExplorer.from_snapshot(snapshot)

    def test_native_empty_snapshot_still_restores(self):
        before = RangeExplorer()
        restored = RangeExplorer.from_snapshot(before.snapshot())
        self.assertEqual(restored.snapshot(), before.snapshot())


if __name__ == "__main__":
    unittest.main()
