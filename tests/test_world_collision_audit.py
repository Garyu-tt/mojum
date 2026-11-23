"""Hermetic, stdlib regression for the independent point-room collision oracle."""
import unittest
from unittest.mock import patch

from mojum.world_room import Room, RoomState, advance, initial_state
from mojum.world_collision_audit import (
    MAX_OBSTACLES, MAX_STEPS, _reference_blocked, audit_path,
    audit_seeded, audit_step,
)


class CollisionAuditTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(4, 4, ((2, 1, 0.001, 2),))
        self.state = initial_state(self.room, 1, 2)

    def test_thin_obstacle_cannot_be_tunneled(self):
        _, r = audit_step(self.room, self.state, dx=2, dy=0)
        self.assertTrue(r['reference_blocked'])
        self.assertTrue(r['runtime_blocked'])
        self.assertTrue(r['agrees'])
        self.assertFalse(r['physical_contact'])
        self.assertEqual(r['visibility'], 'PUBLIC_SYNTHETIC')
        self.assertEqual(len(r['receipt_sha256']), 64)

    def test_tangent_at_obstacle_boundary_is_blocked(self):
        state = initial_state(self.room, 1, 1)
        _, r = audit_step(self.room, state, dx=2, dy=0)
        self.assertTrue(r['reference_blocked'])
        self.assertTrue(r['agrees'])

    def test_just_outside_obstacle_clear(self):
        state = initial_state(self.room, 1, 0.99)
        after, r = audit_step(self.room, state, dx=2, dy=0)
        self.assertFalse(r['reference_blocked'])
        self.assertTrue(r['agrees'])
        self.assertEqual((after.x, after.y), (3, .99))

    def test_diagonal_exact_corner_touch_blocked(self):
        room = Room(4, 4, ((2, 2, 0.25, 0.25),))
        state = initial_state(room, 1, 1)
        _, r = audit_step(room, state, dx=2, dy=2)
        self.assertTrue(r['reference_blocked'])
        self.assertTrue(r['agrees'])

    def test_wall_boundary_vs_outside(self):
        room = Room(4, 4)
        start = initial_state(room, 3, 2)
        _, touch = audit_step(room, start, dx=1, dy=0)
        _, outside = audit_step(room, start, dx=2, dy=0)
        self.assertFalse(touch['reference_blocked'])
        self.assertTrue(outside['reference_blocked'])
        self.assertTrue(touch['agrees'] and outside['agrees'])

    def test_zero_motion_and_seeded_repeatability(self):
        _, r = audit_step(self.room, self.state, dx=0, dy=0)
        self.assertFalse(r['reference_blocked'])
        a = audit_seeded(self.room, self.state, seed=238, count=64)
        b = audit_seeded(self.room, self.state, seed=238, count=64)
        c = audit_seeded(self.room, self.state, seed=239, count=64)
        self.assertEqual(a, b)
        self.assertNotEqual(a['receipts_sha256'], c['receipts_sha256'])
        self.assertEqual(a['checked'], 64)
        self.assertEqual(a['mismatch_count'], 0)
        self.assertEqual(a['mismatch_ticks'], [])

    def test_batch_uses_actual_canonical_revisions(self):
        r = audit_path(self.room, self.state, [(0, 0), (2, 0), (-0.5, 0)])
        self.assertEqual(r['checked'], 3)
        self.assertEqual(r['mismatch_count'], 0)
        self.assertEqual(len(r['summary_sha256']), 64)
        self.assertNotEqual(r['final_revision'], self.state.revision)

    def test_stale_or_corrupt_revision_refused(self):
        bad = RoomState(0, 1, 2, '0' * 64)
        with self.assertRaisesRegex(ValueError, 'revision'):
            audit_step(self.room, bad, dx=1, dy=0)

    def test_boolean_nan_infinite_and_overbudget_moves_fail(self):
        for dx, dy in ((True, 0), (float('nan'), 0),
                       (float('inf'), 0), (900, 900), (1001, 0)):
            with self.subTest(dx=dx, dy=dy), self.assertRaises(ValueError):
                audit_step(self.room, self.state, dx=dx, dy=dy)

    def test_room_obstacle_and_tick_budget_fail_closed(self):
        huge = Room(100_001, 1)
        with self.assertRaisesRegex(ValueError, 'bounds'):
            audit_step(huge, initial_state(huge, 0, 0), dx=0, dy=0)
        room = Room(4, 4, ((2, 1, .001, 2),) * (MAX_OBSTACLES + 1))
        with self.assertRaisesRegex(ValueError, 'bounds'):
            audit_step(room, initial_state(room, 1, 2), dx=0, dy=0)
        state = RoomState(1_000_000, 1, 2, 'x')
        with self.assertRaisesRegex(ValueError, 'state'):
            audit_step(self.room, state, dx=0, dy=0)

    def test_malformed_or_oversized_batch_is_rejected_before_advance(self):
        for steps in ([], [(0, 0)] * (MAX_STEPS + 1),
                      [(0, 0), ('illegal', 0)], [(0,)],
                      [(0, 0), (float('nan'), 0)]):
            with self.subTest(steps=len(steps)), patch(
                'mojum.world_collision_audit.advance'
            ) as canonical:
                with self.assertRaises(ValueError):
                    audit_path(self.room, self.state, steps)
                canonical.assert_not_called()

    def test_seed_strict_and_bound(self):
        for seed in (True, -1, 2**32, '1234'):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                audit_seeded(self.room, self.state, seed=seed)
        for count in (0, -1, 129, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                audit_seeded(self.room, self.state, seed=0, count=count)

    def test_fabricated_runtime_blocked_flag_becomes_mismatch(self):
        real_advance = advance

        def fabricated(*args, **kwargs):
            new_state, frame = real_advance(*args, **kwargs)
            return new_state, {**frame, 'blocked': not frame['blocked']}

        with patch('mojum.world_collision_audit.advance', side_effect=fabricated):
            r = audit_path(self.room, self.state, [(2, 0)])
        self.assertEqual(r['mismatch_count'], 1)
        self.assertEqual(r['mismatch_ticks'], [1])

    def test_fabricated_physical_contact_is_rejected(self):
        real_advance = advance

        def fabricated(*args, **kwargs):
            new_state, frame = real_advance(*args, **kwargs)
            return new_state, {**frame, 'physical_contact': True}

        with patch('mojum.world_collision_audit.advance', side_effect=fabricated):
            with self.assertRaisesRegex(ValueError, 'observation'):
                audit_step(self.room, self.state, dx=0, dy=0)

    def test_input_float_not_decimal_reinterpreted(self):
        # Exactly represent float 0.1 as a binary rational, not 1/10.
        room = Room(1, 1, ((.3, .4, .01, .1),))
        state = initial_state(room, .1, .45)
        self.assertIsInstance(_reference_blocked(room, state, .2, 0), bool)
        a = audit_step(room, state, dx=.2, dy=0)[1]
        b = audit_step(room, state, dx=.2, dy=0)[1]
        self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
