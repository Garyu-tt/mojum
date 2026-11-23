"""Hermetic route witnesses for the existing synthetic point-room fixture."""

import copy
import unittest

from mojum.world_reachability import find_sampled_path, verify_witness
from mojum.world_room import Room, advance, initial_state


class SampledPathTests(unittest.TestCase):
    def test_empty_room_direct_path_and_replay(self):
        room = Room(4, 4)
        report = find_sampled_path(room, (0, 0), (4, 4), columns=4, rows=4)
        self.assertEqual(report["status"], "witness_found")
        self.assertEqual(len(report["path"]), 2)
        self.assertEqual(report["path"][0]["tick"], 0)
        self.assertEqual(report["path"][-1]["tick"], 1)
        self.assertTrue(verify_witness(room, report))

    def test_detour_around_obstacle_is_replayed_by_canonical_fixture(self):
        room = Room(6, 6, ((2.5, 1.5, 1, 3),))
        first = find_sampled_path(room, (1, 3), (5, 3), columns=12, rows=12)
        second = find_sampled_path(room, (1, 3), (5, 3), columns=12, rows=12)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "witness_found")
        self.assertGreater(len(first["path"]), 2)
        self.assertTrue(verify_witness(room, first))
        for before, after in zip(first["path"], first["path"][1:]):
            s = initial_state(room, *before["position"])
            # Independent one-segment replay, regardless of full-route tick.
            nxt, obs = advance(room, s, tick=1,
                               dx=after["position"][0] - s.x,
                               dy=after["position"][1] - s.y,
                               expected_revision=s.revision)
            self.assertFalse(obs["blocked"])
            self.assertEqual([nxt.x, nxt.y], after["position"])

    def test_impenetrable_full_height_wall_is_inconclusive_not_unreachable(self):
        room = Room(6, 6, ((2.5, 0, 1, 6),))
        report = find_sampled_path(room, (1, 3), (5, 3), columns=12, rows=12)
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["path"], [])
        with self.assertRaisesRegex(ValueError, "no route"):
            verify_witness(room, report)

    def test_same_start_goal(self):
        room = Room(2, 2)
        report = find_sampled_path(room, (1, 1), (1, 1))
        self.assertEqual(report["status"], "witness_found")
        self.assertEqual(len(report["path"]), 2)  # Direct zero-displacement tick.
        self.assertTrue(verify_witness(room, report))

    def test_witness_cannot_replay_against_modified_obstacle(self):
        room = Room(6, 6)
        report = find_sampled_path(room, (1, 3), (5, 3))
        altered = Room(6, 6, ((2, 2, 1, 2),))
        with self.assertRaises(ValueError):
            verify_witness(altered, report)

    def test_tampered_revision_position_tick_and_counts_rejected(self):
        room = Room(5, 5)
        report = find_sampled_path(room, (0, 0), (4, 4))
        changes = [
            ("path", lambda x: x[-1].update(revision="0" * 64)),
            ("path", lambda x: x[-1].update(position=[5.0, 5.0])),
            ("path", lambda x: x[-1].update(tick=3)),
            ("visited_vertices", lambda x: None),
            ("grid", lambda x: x.update(columns=33)),
        ]
        for field, mutate in changes:
            forged = copy.deepcopy(report)
            if field == "visited_vertices":
                forged[field] = True
            else:
                mutate(forged[field])
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_witness(room, forged)

    def test_invalid_inputs_rejected_without_mutating_geometry(self):
        room = Room(3, 3, ((1, 1, .5, .5),))
        baseline = room.obstacles
        invalid = [((True, 0), (2, 2), 4, 4),
                   ((float("nan"), 0), (2, 2), 4, 4),
                   ((1, 1), (2, 2), True, 4),
                   ((1, 1), (2, 2), 33, 1),
                   ((1.2, 1.2), (2, 2), 4, 4),
                   ((1, 1), (9, 2), 4, 4)]
        for start, goal, cols, rows in invalid:
            with self.subTest(start=start, goal=goal, columns=cols), self.assertRaises(ValueError):
                find_sampled_path(room, start, goal, columns=cols, rows=rows)
        self.assertEqual(room.obstacles, baseline)

    def test_audit_budgets_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "budget"):
            find_sampled_path(Room(10001, 3), (0, 0), (1, 1))
        with self.assertRaisesRegex(ValueError, "budget"):
            find_sampled_path(Room(3, 3, ((0, 0, .001, .001),) * 129),
                              (1, 1), (2, 2))


if __name__ == "__main__":
    unittest.main()
