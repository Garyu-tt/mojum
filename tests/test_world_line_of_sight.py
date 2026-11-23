"""Hermetic PUBLIC_SYNTHETIC target visibility admission and geometry checks."""

import copy
import hashlib
import json
import math
import unittest

from mojum.world_room import Room, RoomState, advance, initial_state
from mojum.world_line_of_sight import (
    FRAME_PROVENANCE, FRAME_SCHEMA, MAX_DISTANCE_M, MAX_OBSTACLES,
    MAX_TARGETS, read_target_visibility,
)


class TargetLineOfSightTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(12, 9, ((4, 1, 1, 6), (8, 7, 1, 1)))
        self.state = initial_state(self.room, 2, 3)

    def read(self, targets=None, room=None, state=None, **kw):
        room = self.room if room is None else room
        state = self.state if state is None else state
        args = dict(expected_tick=state.tick, expected_revision=state.revision,
                    visibility='PUBLIC_SYNTHETIC', opt_in=True)
        args.update(kw)
        return read_target_visibility(room, state, [('a', 3, 3)] if targets is None else targets, **args)

    def test_clear_multiple_targets_and_source_metadata(self):
        frame = self.read([('north', 2, 8), ('south', 2, 0), ('near', 3, 3)])
        self.assertEqual(frame['schema'], FRAME_SCHEMA)
        self.assertEqual(frame['provenance'], FRAME_PROVENANCE)
        self.assertEqual(frame['tick'], 0)
        self.assertEqual(frame['room_revision'], self.state.revision)
        self.assertEqual(frame['observer_position'], [2, 3])
        self.assertIs(frame['physical_contact'], False)
        self.assertEqual([t['id'] for t in frame['targets']], ['north', 'south', 'near'])
        self.assertTrue(all(t['visible'] for t in frame['targets']))

    def test_obstacle_blocks_thin_wall_without_tunneling(self):
        room = Room(10, 10, ((4, 1, .001, 8),))
        state = initial_state(room, 2, 5)
        target = self.read([('behind', 8, 5)], room=room, state=state)['targets'][0]
        self.assertFalse(target['visible'])
        self.assertEqual(target['blocking_obstacle_index'], 0)
        self.assertEqual(target['distance_m'], 6)

    def test_diagonal_corner_graze_counts_as_occluded(self):
        room = Room(6, 6, ((3, 3, 1, 1),))
        state = initial_state(room, 1, 5)
        grazing = self.read([('grazing', 5, 1)], room=room, state=state)['targets'][0]
        self.assertFalse(grazing['visible'])
        self.assertEqual(grazing['blocking_obstacle_index'], 0)

    def test_strict_near_miss_remains_visible(self):
        room = Room(6, 6, ((3, 3, 1, 1),))
        state = initial_state(room, 1, 1)
        free = self.read([('free', 5, 2.999)], room=room, state=state)['targets'][0]
        self.assertTrue(free['visible'])
        self.assertIsNone(free['blocking_obstacle_index'])

    def test_target_inside_or_on_obstacle_is_occluded(self):
        for point in ((4, 3), (5, 3), (4.5, 3)):
            with self.subTest(point=point):
                item = self.read([('inside', *point)])['targets'][0]
                self.assertFalse(item['visible'])

    def test_reported_blocker_is_first_declared_not_nearest(self):
        room = Room(12, 10, ((8, 4, 1, 2), (4, 4, 1, 2)))
        state = initial_state(room, 2, 5)
        frame = self.read([('goal', 10, 5)], room=room, state=state)
        self.assertEqual(frame['targets'][0]['blocking_obstacle_index'], 0)

    def test_equal_position_and_room_boundary(self):
        room = Room(2, 2)
        state = initial_state(room, 0, 2)
        frame = self.read([('same', 0, 2), ('corner', 2, 0)], room=room, state=state)
        self.assertTrue(all(t['visible'] for t in frame['targets']))
        self.assertEqual(frame['targets'][0]['distance_m'], 0)
        self.assertTrue(math.isclose(frame['targets'][1]['distance_m'], math.sqrt(8)))

    def test_world_state_after_canonical_advance(self):
        after, observation = advance(self.room, self.state, tick=1, dx=1, dy=0,
                                     expected_revision=self.state.revision)
        frame = self.read([('east', 8, 3)], state=after)
        self.assertEqual(frame['tick'], observation['tick'])
        self.assertEqual(frame['room_revision'], observation['revision'])
        self.assertFalse(frame['targets'][0]['visible'])

    def test_no_movement_detached_and_checksum_reproducible(self):
        before = copy.deepcopy((self.room, self.state))
        frame = self.read([('a', 3, 3), ('b', 8, 5)])
        body = {k: v for k, v in frame.items() if k != 'frame_sha256'}
        raw = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False)
        self.assertEqual(frame['frame_sha256'], hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual((self.room, self.state), before)
        frame['targets'][0]['visible'] = False
        self.assertTrue(self.read()['targets'][0]['visible'])

    def test_reject_missing_or_forged_opt_in_visibility(self):
        for opt_in, label in ((False, 'PUBLIC_SYNTHETIC'), (1, 'PUBLIC_SYNTHETIC'),
                              (True, 'PRIVATE'), (True, 'public_synthetic'),
                              (True, None), (True, 1)):
            with self.subTest(opt_in=opt_in, label=label), self.assertRaises(ValueError):
                self.read(opt_in=opt_in, visibility=label)

    def test_reject_stale_expected_tick_and_revision(self):
        for kw in ({'expected_tick': 1}, {'expected_tick': True},
                   {'expected_tick': None}, {'expected_revision': 'bad'},
                   {'expected_revision': self.state.revision.upper()}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.read(**kw)
        corrupt_state = RoomState(0, 3, 3, self.state.revision)
        with self.assertRaisesRegex(ValueError, 'revision'):
            self.read(state=corrupt_state)

    def test_reject_invalid_source_state_and_excessive_tick(self):
        for bad in (RoomState(True, 2, 3, self.state.revision),
                    RoomState(-1, 2, 3, self.state.revision),
                    RoomState(10_000_001, 2, 3, self.state.revision),
                    RoomState(0, float('nan'), 3, self.state.revision),
                    RoomState(0, False, 3, self.state.revision),
                    RoomState(0, 4, 3, self.state.revision)):
            with self.subTest(state=bad), self.assertRaises(ValueError):
                self.read(state=bad)

    def test_reject_corrupted_frozen_room(self):
        altered = Room(10, 10)
        state = initial_state(altered, 2, 3)
        object.__setattr__(altered, 'obstacles', ((0, 0, -1, 2),))
        with self.assertRaisesRegex(ValueError, 'geometry'):
            self.read(room=altered, state=state)
        altered2 = Room(10, 10)
        state2 = initial_state(altered2, 2, 3)
        object.__setattr__(altered2, 'width', True)
        with self.assertRaises(ValueError):
            self.read(room=altered2, state=state2)

    def test_reject_unbounded_room_and_obstacle_count(self):
        huge = Room(10001, 10)
        state = initial_state(huge, 1, 1)
        with self.assertRaises(ValueError):
            self.read(room=huge, state=state)
        room = Room(20, 20, tuple((1, 1, 1, 1) for _ in range(MAX_OBSTACLES + 1)))
        state = initial_state(room, 3, 3)
        with self.assertRaises(ValueError):
            self.read(room=room, state=state)

    def test_reject_malformed_target_collections_and_types(self):
        invalid = ([], [('a', 3, 3)] * (MAX_TARGETS + 1),
                   [('a', 3, 3), ('a', 4, 4)], [('A', 3, 3)],
                   [('a'*41, 3, 3)], [(1, 3, 3)], [('a', True, 3)],
                   [('a', math.nan, 3)], [('a', math.inf, 3)],
                   [('a', -1, 3)], [('a', 20, 3)], [('a', 3)],
                   ['a'], [('a', 10**999, 3)])
        for item in invalid:
            with self.subTest(item_type=type(item).__name__, item_repr=str(item)[:40]):
                with self.assertRaises(ValueError):
                    self.read(item)
        for nonlist in (None, 'a', (), ((x for x in (('a', 3, 3),))), {}):
            with self.subTest(nonlist=type(nonlist).__name__), self.assertRaises(ValueError):
                read_target_visibility(self.room, self.state, nonlist,
                    expected_tick=0, expected_revision=self.state.revision,
                    visibility='PUBLIC_SYNTHETIC', opt_in=True)

    def test_distance_limit_and_no_partial_result(self):
        room = Room(2000, 5)
        state = initial_state(room, 0, 1)
        self.assertTrue(self.read([('edge', MAX_DISTANCE_M, 1)], room=room,
                                  state=state)['targets'][0]['visible'])
        with self.assertRaisesRegex(ValueError, 'distance'):
            self.read([('good', 2, 1), ('bad', 1000.00001, 1)],
                      room=room, state=state)

    def test_strict_types_and_impersonation(self):
        class ForgedRoom(Room):
            pass
        fake = ForgedRoom(2, 2)
        state = initial_state(fake, 1, 1)
        with self.assertRaises(ValueError):
            self.read(room=fake, state=state)
        class ForgedState(RoomState):
            pass
        state2 = ForgedState(self.state.tick, self.state.x, self.state.y,
                             self.state.revision)
        with self.assertRaises(ValueError):
            self.read(state=state2)


if __name__ == '__main__':
    unittest.main()
