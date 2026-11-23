"""Hermetic synthetic-only object interaction contract regressions."""
import copy
from dataclasses import replace
import json
import math
import unittest

from mojum.world_room import Room, RoomState, advance, initial_state
from mojum.world_objects import (MAX_OBJECTS, REACH_M, SCHEMA, ObjectState,
                                 advance_objects, initial_objects,
                                 restore_objects, snapshot_objects)


class ObjectInteractionTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(4, 4, ((1.0, 1.0, 0.01, 0.8),))
        self.start = initial_state(self.room, 0.5, 0.5)
        self.objects = initial_objects(self.room, self.start,
                                       (("b", 0.5, 0.75), ("a", 0.7, 0.5)))

    def step(self, room=None, state=None, objects=None, *, tick=None,
             dx=0, dy=0, action="wait", object_id=None, place=None, **overrides):
        room = self.room if room is None else room
        state = self.start if state is None else state
        objects = self.objects if objects is None else objects
        args = dict(tick=state.tick + 1 if tick is None else tick, dx=dx, dy=dy,
                    expected_room_revision=state.revision,
                    expected_objects_revision=objects.revision,
                    action=action, object_id=object_id, place=place)
        args.update(overrides)
        return advance_objects(room, state, objects, **args)

    def test_canonical_fixture_order_and_detachment(self):
        source = [["z", 0.5, 0.75], ["a", 0.7, 0.5]]
        objects = initial_objects(self.room, self.start, source)
        source[0][1] = 999
        self.assertEqual([i[0] for i in objects.items], ["a", "z"])
        self.assertEqual(objects.items[-1][1], 0.5)
        self.assertEqual(objects.revision, initial_objects(self.room, self.start,
                          [["a", 0.7, 0.5], ["z", 0.5, 0.75]]).revision)

    def test_pick_then_place_across_exact_ticks(self):
        r1, o1, e1 = self.step(action="pick", object_id="a")
        self.assertEqual(o1.carried, "a")
        self.assertEqual(o1.items[0], ("a", None, None))
        self.assertEqual(e1["tick"], 1)
        self.assertFalse(e1["physical_contact"])
        r2, o2, e2 = self.step(state=r1, objects=o1, action="place",
                               object_id="a", place=[0.6, 0.6])
        self.assertIsNone(o2.carried)
        self.assertEqual(o2.items[0], ("a", 0.6, 0.6))
        self.assertEqual(r2.tick, o2.tick)
        self.assertNotEqual(o1.revision, o2.revision)
        self.assertEqual(e2["provenance"], "deterministic-point-room-objects/v1")

    def test_room_motion_is_authoritative_before_pick(self):
        with self.assertRaisesRegex(ValueError, "reach"):
            self.step(dx=0, dy=-0.25, action="pick", object_id="b")
        _, objects, event = self.step(dx=0.20, dy=0, action="pick", object_id="a")
        self.assertEqual(objects.carried, "a")
        self.assertEqual(event["actor_position"], [0.7, 0.5])

    def test_blocked_movement_still_consumes_one_tick(self):
        r1, o1, event = self.step(dx=0.6, dy=0.6, action="wait")
        self.assertTrue(event["move_blocked"])
        self.assertEqual((r1.x, r1.y), (0.5, 0.5))
        self.assertEqual(o1.tick, 1)
        self.assertIsNone(o1.carried)

    def test_occluded_pick_even_within_reach(self):
        room = Room(2, 2, ((0.6, 0.25, 0.005, 0.5),))
        start = initial_state(room, 0.5, 0.5)
        objects = initial_objects(room, start, (("token", 0.7, 0.5),))
        with self.assertRaisesRegex(ValueError, "occluded"):
            self.step(room, start, objects, action="pick", object_id="token")

    def test_occluded_place_even_within_reach(self):
        room = Room(2, 2, ((0.6, 0.25, 0.005, 0.5),))
        start = initial_state(room, 0.5, 0.5)
        objects = initial_objects(room, start, (("token", 0.5, 0.5),))
        r1, o1, _ = self.step(room, start, objects, action="pick", object_id="token")
        with self.assertRaisesRegex(ValueError, "reachable"):
            self.step(room, r1, o1, action="place", object_id="token", place=(0.7, 0.5))

    def test_occupied_carry_slot_and_absent_items(self):
        r1, o1, _ = self.step(action="pick", object_id="a")
        with self.assertRaisesRegex(ValueError, "occupied"):
            self.step(state=r1, objects=o1, action="pick", object_id="b")
        with self.assertRaisesRegex(ValueError, "not carried"):
            self.step(state=r1, objects=o1, action="place", object_id="b", place=(0.5, 0.5))
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.step(action="pick", object_id="missing")
        with self.assertRaisesRegex(ValueError, "not carried"):
            self.step(action="place", object_id="a", place=(0.5, 0.5))

    def test_drop_cannot_teleport_or_enter_obstacle(self):
        r1, o1, _ = self.step(action="pick", object_id="a")
        for target in ((1.005, 1.1), (3.0, 3.0), (-0.1, 0.5)):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.step(state=r1, objects=o1, action="place",
                          object_id="a", place=target)

    def test_stale_cas_tick_and_room_drift_fail(self):
        r1, o1, _ = self.step()
        with self.assertRaises(ValueError):
            self.step(state=r1, objects=self.objects)
        with self.assertRaises(ValueError):
            self.step(expected_objects_revision="0" * 64)
        with self.assertRaises(ValueError):
            self.step(expected_room_revision="f" * 64)
        with self.assertRaises(ValueError):
            self.step(tick=3)
        with self.assertRaises(ValueError):
            self.step(room=Room(4.1, 4, self.room.obstacles))
        self.assertEqual(o1.tick, 1)

    def test_forged_room_position_or_revision_fail(self):
        forgery = replace(self.start, x=1.5)
        with self.assertRaises(ValueError):
            self.step(state=forgery)
        forgery = replace(self.start, revision="0" * 64)
        with self.assertRaises(ValueError):
            self.step(state=forgery)
        with self.assertRaises(ValueError):
            self.step(state=replace(self.start, tick=True))

    def test_forged_object_position_carried_or_revision_fail(self):
        for forgery in (
                replace(self.objects, items=(("a", 9, 9), ("b", 0.5, 0.75))),
                replace(self.objects, carried="a"),
                replace(self.objects, revision="0" * 64),
                replace(self.objects, tick=True),
                replace(self.objects, items=(["a", 0.7, 0.5], ("b", 0.5, 0.75)))):
            with self.subTest(forgery=repr(forgery)[:70]), self.assertRaises(ValueError):
                self.step(objects=forgery)

    def test_bad_action_and_unexpected_fields_fail(self):
        for changes in ({"action": "pickup"}, {"action": True},
                        {"action": "wait", "object_id": "a"},
                        {"action": "pick", "object_id": "a", "place": (0.5, 0.5)},
                        {"action": "pick", "object_id": True},
                        {"action": "place", "object_id": "a", "place": (math.nan, 0.5)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.step(**changes)

    def test_malformed_fixture_ids_coords_and_bounds(self):
        invalid = [(("a", 0.5, 0.5), ("a", 0.6, 0.6)),
                   (("bad id", 0.5, 0.5),),
                   (("a", True, 0.5),),
                   (("a", math.inf, 0.5),),
                   (("a", 1.0, 1.2),),
                   (("a", -1, 0.5),),
                   (("a", 0.5, None),),
                   (("a", 10 ** 10000, 0.5),),
                   tuple(("x" + str(i), 0.5, 0.5) for i in range(MAX_OBJECTS + 1))]
        for fixture in invalid:
            with self.subTest(index=invalid.index(fixture)), self.assertRaises(ValueError):
                initial_objects(self.room, self.start, fixture)

    def test_snapshot_roundtrip_and_tamper(self):
        r1, o1, _ = self.step(action="pick", object_id="a")
        wire = json.loads(json.dumps(snapshot_objects(self.room, o1), sort_keys=True))
        self.assertEqual(wire["schema"], SCHEMA)
        self.assertEqual(restore_objects(self.room, r1, wire), o1)
        for field, value in (("schema", "future"), ("tick", True),
                             ("carried", "b"), ("revision", "0"*64),
                             ("room_revision", "1"*64), ("items", [])):
            edited = copy.deepcopy(wire)
            edited[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                restore_objects(self.room, r1, edited)
        edited = copy.deepcopy(wire)
        edited["unexpected"] = 1
        with self.assertRaises(ValueError):
            restore_objects(self.room, r1, edited)

    def test_deterministic_replay_and_inputs_unchanged(self):
        a = self.step(action="pick", object_id="a")
        b = self.step(action="pick", object_id="a")
        self.assertEqual(a, b)
        self.assertIsNone(self.objects.carried)
        self.assertEqual(self.start.tick, 0)
        self.assertEqual(self.objects.items[0], ("a", 0.7, 0.5))
        with self.assertRaises(ValueError):
            self.step(action="pick", object_id="b", dx=1001)
        self.assertEqual(self.objects.tick, 0)

    def test_sequential_wait_replay_keeps_items_stationary(self):
        room_state, obj_state = self.start, self.objects
        for tick in range(1, 8):
            room_state, obj_state, event = self.step(state=room_state, objects=obj_state,
                                                     tick=tick, action="wait", dx=0.1, dy=0)
            self.assertEqual(event["tick"], tick)
            self.assertEqual(obj_state.items, self.objects.items)
            self.assertEqual(obj_state.room_revision, room_state.revision)


if __name__ == "__main__":
    unittest.main()
