"""Hermetic synthetic-only coverage of the optional world/physics tick barrier."""

import copy
import json
import unittest

from mojum.physics_observation import validate_virtual_physics_observation
from mojum.world_physics_barrier import (
    align_world_physics_frame,
    body_frame_revision,
    verify_barrier_receipt,
)
from mojum.world_room import Room, RoomState, advance, initial_state


DT_NS = 1_000_000


def physics_packet(tick=0, *, backend="fixture"):
    return {
        "schema": "mojum.virtual-physics-observation/1",
        "tick": tick,
        "time_ns": tick * DT_NS,
        "backend": backend,
        "synthetic": True,
        "physical_contact": False,
        "physical_actuation": False,
        "joints": [{"id": "joint.1", "position_rad": 0.125, "velocity_rad_s": 0.0}],
        "virtual_contacts": [],
    }


class JointRevisionBarrierTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(4, 4, ((2, 1, 0.1, 2),))
        self.state = initial_state(self.room, 1, 2)
        self.body = physics_packet()

    def align(self, *, state=None, body=None, room=None, previous=None, dt_ns=DT_NS,
              expected_room_revision=None, expected_body_revision=None):
        state = self.state if state is None else state
        body = self.body if body is None else body
        room = self.room if room is None else room
        return align_world_physics_frame(
            room, state, body, dt_ns=dt_ns,
            expected_room_revision=(state.revision if expected_room_revision is None
                                    else expected_room_revision),
            expected_body_revision=(body_frame_revision(body) if expected_body_revision is None
                                    else expected_body_revision),
            previous=previous,
        )

    def test_exact_alignment_repeatable_and_json_round_trip(self):
        receipt = self.align()
        self.assertEqual(receipt, self.align())
        self.assertEqual(receipt["time_ns"], 0)
        self.assertEqual(receipt["room_revision"], self.state.revision)
        self.assertEqual(receipt["body_revision"], body_frame_revision(self.body))
        self.assertIsNone(receipt["previous_commit"])
        self.assertTrue(verify_barrier_receipt(json.loads(json.dumps(receipt))))
        self.assertEqual(validate_virtual_physics_observation(self.body), self.body)

    def test_advancement_and_causal_chain(self):
        first = self.align()
        next_state, measured = advance(self.room, self.state, tick=1, dx=0.5, dy=0,
                                       expected_revision=self.state.revision)
        self.assertFalse(measured["blocked"])
        second = self.align(state=next_state, body=physics_packet(1), previous=first)
        self.assertEqual(second["previous_commit"], first["commit"])
        self.assertEqual(second["time_ns"], DT_NS)
        self.assertTrue(verify_barrier_receipt(second))
        with self.assertRaisesRegex(ValueError, "duplicate|skipped|reordered"):
            self.align(state=next_state, body=physics_packet(1), previous=second)

    def test_mismatched_or_replayed_clocks_fail(self):
        body = copy.deepcopy(self.body)
        body["tick"] = 1
        with self.assertRaisesRegex(ValueError, "clock mismatch"):
            self.align(body=body)
        body["tick"] = 0
        body["time_ns"] = DT_NS
        with self.assertRaisesRegex(ValueError, "clock mismatch"):
            self.align(body=body)
        with self.assertRaisesRegex(ValueError, "step duration"):
            self.align(dt_ns=True)
        with self.assertRaisesRegex(ValueError, "step duration"):
            self.align(dt_ns=999)

    def test_stale_forged_and_wrong_geometry_fail(self):
        with self.assertRaisesRegex(ValueError, "room revision"):
            self.align(expected_room_revision="0" * 64)
        forged = RoomState(0, 3.0, 3.0, self.state.revision)
        with self.assertRaisesRegex(ValueError, "room revision"):
            self.align(state=forged)
        with self.assertRaisesRegex(ValueError, "room revision"):
            self.align(room=Room(4, 4))
        outside = RoomState(0, -1, 2, self.state.revision)
        with self.assertRaisesRegex(ValueError, "room state"):
            self.align(state=outside)

    def test_body_revision_is_independent_cas(self):
        original = body_frame_revision(self.body)
        mutated = copy.deepcopy(self.body)
        mutated["joints"][0]["position_rad"] = 0.25
        with self.assertRaisesRegex(ValueError, "physics revision"):
            self.align(body=mutated, expected_body_revision=original)
        self.assertEqual(original, body_frame_revision(self.body))
        forged = copy.deepcopy(self.body)
        forged["physical_actuation"] = True
        with self.assertRaises(ValueError):
            self.align(body=forged)
        with self.assertRaises(ValueError):
            self.align(expected_body_revision="bad")

    def test_receipt_is_detached_from_mutable_packet(self):
        receipt = self.align()
        self.body["joints"][0]["position_rad"] = -2
        self.assertTrue(verify_barrier_receipt(receipt))
        self.assertNotEqual(body_frame_revision(self.body), receipt["body_revision"])

    def test_tampered_previous_receipt_and_gap_fail(self):
        first = self.align()
        step1, _ = advance(self.room, self.state, tick=1, dx=0, dy=0,
                           expected_revision=self.state.revision)
        modified = dict(first)
        modified["room_revision"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.align(state=step1, body=physics_packet(1), previous=modified)
        modified = dict(first)
        modified["extra"] = "untrusted"
        with self.assertRaisesRegex(ValueError, "fields"):
            verify_barrier_receipt(modified)
        step2, _ = advance(self.room, step1, tick=2, dx=0, dy=0,
                           expected_revision=step1.revision)
        with self.assertRaisesRegex(ValueError, "duplicate|skipped|reordered"):
            self.align(state=step2, body=physics_packet(2), previous=first)

    def test_chain_pins_room_geometry_and_backend(self):
        first = self.align()
        changed_room = Room(4, 4)
        initial = initial_state(changed_room, 1, 2)
        state1, _ = advance(changed_room, initial, tick=1,
                            dx=0, dy=0, expected_revision=initial.revision)
        with self.assertRaisesRegex(ValueError, "geometry"):
            self.align(room=changed_room, state=state1, body=physics_packet(1), previous=first)
        step1, _ = advance(self.room, self.state, tick=1, dx=0, dy=0,
                           expected_revision=self.state.revision)
        with self.assertRaisesRegex(ValueError, "backend"):
            self.align(state=step1, body=physics_packet(1, backend="mujoco"), previous=first)

    def test_large_clock_and_hostile_receipt_types(self):
        state = RoomState(2**63 - 1, self.state.x, self.state.y, self.state.revision)
        with self.assertRaisesRegex(ValueError, "overflows"):
            self.align(state=state)
        receipt = self.align()
        for key, value in (("tick", True), ("time_ns", False),
                           ("previous_commit", "A" * 64), ("dt_ns", 1.5),
                           ("schema", "not-the-schema")):
            corrupted = dict(receipt)
            corrupted[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_barrier_receipt(corrupted)


    def test_receipt_rejects_spoofed_key_types_before_comparison(self):
        receipt = self.align()

        class EqualLookingKey(str):
            pass

        # This used to pass set equality and even the checksum comparison.
        alias = {EqualLookingKey(key) if key == "schema" else key: value
                 for key, value in receipt.items()}
        with self.assertRaisesRegex(ValueError, "fields"):
            verify_barrier_receipt(alias)

        class ExplosiveKey(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                raise AssertionError("untrusted key equality must not run")

        hostile = {ExplosiveKey(key) if key == "schema" else key: value
                   for key, value in receipt.items()}
        with self.assertRaisesRegex(ValueError, "fields"):
            verify_barrier_receipt(hostile)


if __name__ == "__main__":
    unittest.main()
