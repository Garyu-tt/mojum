"""Offline contract tests for the isolated point-room range sensor."""
import copy
import hashlib
import json
import unittest

from mojum.world_room import Room, RoomState, advance, initial_state
from mojum.world_range_sensor import (
    DIRECTIONS, FRAME_PROVENANCE, FRAME_SCHEMA, read_range_frame,
)


class RangeSensorTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(10, 8, ((4, 1, .1, 6), (7, 3, .1, .1)))
        self.state = initial_state(self.room, 2, 3)

    def read(self, room=None, state=None, **kwargs):
        room = self.room if room is None else room
        state = self.state if state is None else state
        return read_range_frame(room, state, expected_revision=state.revision, **kwargs)

    def test_exact_four_ray_distances_and_provenance(self):
        frame = self.read()
        self.assertEqual(frame['schema'], FRAME_SCHEMA)
        self.assertEqual(frame['provenance'], FRAME_PROVENANCE)
        self.assertEqual(frame['tick'], 0)
        self.assertEqual(frame['room_revision'], self.state.revision)
        self.assertEqual(frame['position'], [2, 3])
        self.assertIs(frame['physical_contact'], False)
        self.assertEqual([x['direction'] for x in frame['rays']], list(DIRECTIONS))
        east, north, west, south = frame['rays']
        self.assertEqual((east['distance_m'], east['hit'], east['obstacle_index']), (2, 'obstacle', 0))
        self.assertEqual((north['distance_m'], north['hit']), (5, 'boundary'))
        self.assertEqual((west['distance_m'], west['hit']), (2, 'boundary'))
        self.assertEqual((south['distance_m'], south['hit']), (3, 'boundary'))
        self.assertIsNone(north['obstacle_index'])

    def test_obstacles_in_all_cardinal_directions(self):
        room = Room(10, 10, ((1, 4, 1, 2), (4, 7, 2, 1),
                             (7, 4, 1, 2), (4, 2, 2, 1)))
        state = initial_state(room, 5, 5)
        rays = self.read(room, state)['rays']
        self.assertEqual(
            [(r['direction'], r['distance_m'], r['hit'], r['obstacle_index'])
             for r in rays],
            [('east', 2, 'obstacle', 2), ('north', 2, 'obstacle', 1),
             ('west', 3, 'obstacle', 0), ('south', 2, 'obstacle', 3)],
        )

    def test_checksum_canonical_and_deterministic(self):
        original = self.read()
        self.assertEqual(original, self.read())
        without_hash = {k: v for k, v in original.items() if k != 'frame_sha256'}
        encoded = json.dumps(without_hash, sort_keys=True, separators=(',', ':'), allow_nan=False)
        self.assertEqual(original['frame_sha256'], hashlib.sha256(encoded.encode()).hexdigest())
        self.assertEqual(self.state, initial_state(self.room, 2, 3))

    def test_range_limit_and_exact_limit_hit(self):
        tiny = self.read(max_range_m=1)
        self.assertTrue(all(ray['distance_m'] == 1 and ray['hit'] == 'range_limit'
                            and ray['obstacle_index'] is None for ray in tiny['rays']))
        at_edge = self.read(max_range_m=2)
        self.assertEqual(at_edge['rays'][0]['hit'], 'obstacle')
        self.assertEqual(at_edge['rays'][2]['hit'], 'boundary')
        self.assertEqual(at_edge['rays'][1]['hit'], 'range_limit')

    def test_obstacle_barely_grazed_and_strict_near_miss(self):
        room = Room(5, 5, ((2, 2, .001, .001),))
        grazing = initial_state(room, 1, 2)
        hit = self.read(room, grazing)
        self.assertEqual((hit['rays'][0]['distance_m'], hit['rays'][0]['hit']), (1, 'obstacle'))
        miss = initial_state(room, 1, 1.999)
        rays = self.read(room, miss)['rays']
        self.assertEqual((rays[0]['distance_m'], rays[0]['hit']), (4, 'boundary'))

    def test_nearest_obstacle_and_stable_index_tie(self):
        room = Room(6, 6, ((4, 2, 1, 1), (3, 2, .5, 1), (3, 2, .5, 1)))
        start = initial_state(room, 1, 2.5)
        ray = self.read(room, start)['rays'][0]
        self.assertEqual((ray['distance_m'], ray['obstacle_index']), (2, 1))
        self.assertEqual(ray['hit'], 'obstacle')

    def test_sensor_reports_current_tick_after_measured_step(self):
        next_state, observation = advance(self.room, self.state,
            tick=1, dx=1, dy=0, expected_revision=self.state.revision)
        frame = self.read(state=next_state)
        self.assertEqual(frame['tick'], observation['tick'])
        self.assertEqual(frame['room_revision'], observation['revision'])
        self.assertEqual(frame['rays'][0]['distance_m'], 1)
        self.assertEqual(frame['position'], observation['position'])

    def test_bad_or_stale_revisions_rejected(self):
        for revision in ('bad', '', None, 123, True, self.state.revision.upper()):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                read_range_frame(self.room, self.state, expected_revision=revision)
        changed = Room(10, 8, ((4, 1, .2, 6),))
        with self.assertRaisesRegex(ValueError, 'revision'):
            self.read(room=changed)
        corrupt_state = RoomState(self.state.tick, self.state.x + 1,
                                  self.state.y, self.state.revision)
        with self.assertRaisesRegex(ValueError, 'revision'):
            self.read(state=corrupt_state)

    def test_reject_invalid_state_without_advancing(self):
        states = [RoomState(True, 2, 3, self.state.revision),
                  RoomState(-1, 2, 3, self.state.revision),
                  RoomState(0, float('nan'), 3, self.state.revision),
                  RoomState(0, 4, 3, self.state.revision),
                  RoomState(0, 100, 3, self.state.revision)]
        for bad in states:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.read(state=bad)
        self.assertEqual(self.state.tick, 0)

    def test_range_numeric_guards(self):
        for bad in (None, True, False, '10', 0, -1, 1001, float('nan'), float('inf'), 10**999):
            with self.subTest(bad=type(bad).__name__), self.assertRaises(ValueError):
                self.read(max_range_m=bad)
        self.assertEqual(self.read(max_range_m=1000)['max_range_m'], 1000)
        self.assertEqual(self.read(max_range_m=0.001)['max_range_m'], 0.001)

    def test_on_wall_zero_distance_but_no_physical_contact(self):
        room = Room(2, 2)
        start = initial_state(room, 0, 2)
        frame = self.read(room, start)
        self.assertEqual((frame['rays'][2]['distance_m'], frame['rays'][2]['hit']), (0, 'boundary'))
        self.assertEqual((frame['rays'][1]['distance_m'], frame['rays'][1]['hit']), (0, 'boundary'))
        self.assertFalse(frame['physical_contact'])

    def test_input_is_not_mutated_and_fresh_frame_is_returned(self):
        before = copy.deepcopy((self.room, self.state))
        first = self.read()
        first['rays'][0]['hit'] = 'tampered'
        self.assertEqual((self.room, self.state), before)
        self.assertEqual(self.read()['rays'][0]['hit'], 'obstacle')


if __name__ == '__main__':
    unittest.main()
