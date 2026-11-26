"""Hermetic replay/tamper regressions for issue #7 point-room-only trace."""
import copy
import json
import unittest

from mojum.world_room import Room
from mojum.world_trace import (
    TraceError, MAX_STEPS, make_trace, verify_trace, verify_trace_bytes,
)


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(4, 4, ((2.004, 1, .001, 2),))
        self.trace = make_trace(self.room, (1, 2), ((.5, 0), (2, 0), (0, -.25)))

    def test_exact_replay_and_blocked_swept_collision(self):
        frames = self.trace["frames"]
        self.assertFalse(frames[0]["observation"]["blocked"])
        self.assertTrue(frames[1]["observation"]["blocked"])
        self.assertEqual(frames[1]["observation"]["position"], [1.5, 2])
        state = verify_trace_bytes(json.dumps(self.trace).encode())
        self.assertEqual(state.tick, 3)
        self.assertEqual(state.revision, self.trace["final_revision"])

    def test_teleport_and_lying_blocked_flag_fail(self):
        for mutation in (
            lambda t: t["frames"][1]["observation"]["position"].__setitem__(0, 2.0),
            lambda t: t["frames"][1]["observation"].__setitem__("blocked", False),
            lambda t: t["frames"][1]["observation"].__setitem__("physical_contact", True),
        ):
            with self.subTest(mutation=mutation):
                t = copy.deepcopy(self.trace)
                mutation(t)
                with self.assertRaisesRegex(TraceError, "observation differs"):
                    verify_trace(t)

    def test_stale_revision_gap_and_final_head_fail(self):
        for path, value in (("expected_revision", "0" * 64), ("tick", 5)):
            with self.subTest(path=path):
                t = copy.deepcopy(self.trace)
                t["frames"][1][path] = value
                with self.assertRaises(TraceError):
                    verify_trace(t)
        t = copy.deepcopy(self.trace)
        t["frames"].pop(0)
        with self.assertRaises(TraceError):
            verify_trace(t)
        t = copy.deepcopy(self.trace)
        t["final_revision"] = "0" * 64
        with self.assertRaisesRegex(TraceError, "final revision"):
            verify_trace(t)

    def test_invalid_numeric_types_and_unknown_keys_fail(self):
        for field, value in (("dx", True), ("dy", float("nan")), ("dx", 1e308)):
            with self.subTest(field=field, value=value):
                t = copy.deepcopy(self.trace)
                t["frames"][0][field] = value
                with self.assertRaises(TraceError):
                    verify_trace(t)
        t = copy.deepcopy(self.trace)
        t["frames"][0]["observation"]["blocked"] = 1
        with self.assertRaises(TraceError):
            verify_trace(t)
        t = copy.deepcopy(self.trace)
        t["frames"][0]["debug_secret"] = "not allowed"
        with self.assertRaises(TraceError):
            verify_trace(t)

    def test_direct_api_rejects_forged_mapping_keys(self):
        class Alias(str):
            pass

        class ExplodingKey(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                raise AssertionError("untrusted key comparison executed")

        for level, name in (("trace", "schema"), ("room", "width"),
                            ("frame", "tick"), ("observation", "tick")):
            for key_type in (Alias, ExplodingKey):
                with self.subTest(level=level, key_type=key_type.__name__):
                    trace = make_trace(Room(4, 4), (0, 0), [(1, 1)])
                    mapping = (trace if level == "trace" else
                               trace["room"] if level == "room" else
                               trace["frames"][0] if level == "frame" else
                               trace["frames"][0]["observation"])
                    value = mapping.pop(name)
                    mapping[key_type(name)] = value
                    with self.assertRaises(TraceError):
                        verify_trace(trace)

    def test_direct_api_rejects_non_string_schema_before_comparison(self):
        class ForgedSchema:
            def __eq__(self, other):
                return other == "mojum.world.point-room-trace/1"

        class HostileSchema:
            def __eq__(self, other):
                raise AssertionError("untrusted schema equality invoked")

        for impostor in (ForgedSchema(), HostileSchema()):
            with self.subTest(schema_type=type(impostor).__name__):
                trace = copy.deepcopy(self.trace)
                trace["schema"] = impostor
                with self.assertRaisesRegex(TraceError, "unsupported trace schema"):
                    verify_trace(trace)

    def test_duplicate_and_nonfinite_json_fail(self):
        for raw in (
            b'{"schema": 1, "schema": 2}',
            b'{"schema": NaN}',
            b'{"schema": Infinity}',
            b'\xff',
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(TraceError):
                    verify_trace_bytes(raw)

    def test_limits_no_silent_prefix_acceptance(self):
        with self.assertRaises(TraceError):
            make_trace(self.room, (1, 2), [(0, 0)] * (MAX_STEPS + 1))
        t = copy.deepcopy(self.trace)
        t["frames"].append({})
        with self.assertRaises(TraceError):
            verify_trace(t)
        with self.assertRaises(TraceError):
            verify_trace_bytes(b" " * (512 * 1024 + 1))

    def test_empty_trace_and_room_tampering(self):
        t = make_trace(Room(4, 4), (1, 2), [])
        self.assertEqual(verify_trace(t).tick, 0)
        t = copy.deepcopy(self.trace)
        t["room"]["width"] = 5
        with self.assertRaises(TraceError):
            verify_trace(t)

    def test_input_room_snapshot_detached(self):
        source = [[2.004, 1, .001, 2]]
        room = Room(4, 4, source)
        t = make_trace(room, [1, 2], [])
        source[0][0] = 0
        self.assertEqual(t["room"]["obstacles"][0][0], 2.004)


if __name__ == "__main__":
    unittest.main()
