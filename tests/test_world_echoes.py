import copy
import unittest

from mojum.world_room import Room, initial_state, advance
from mojum.world_echoes import (
    EchoError, EVENT_SCHEMA, OBSERVATION_SCHEMA, observe_first_order_echoes,
)


def event(event_id="voice", *, tick=0, x=5.0, y=2.0, level=1000):
    return {
        "schema": EVENT_SCHEMA,
        "event_id": event_id,
        "source_tick": tick,
        "x": x,
        "y": y,
        "source_level_milli": level,
    }


class WorldEchoTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(10.0, 10.0)
        self.state = initial_state(self.room, 5.0, 5.0)

    def sense(self, events, **kwargs):
        return observe_first_order_echoes(
            self.room, self.state, events,
            expected_revision=self.state.revision, **kwargs,
        )

    def test_schema_and_empty_observation(self):
        result = self.sense([])
        self.assertEqual(result["schema"], OBSERVATION_SCHEMA)
        self.assertEqual(result["events"], [])
        self.assertEqual(len(result["observation_sha256"]), 64)

    def test_first_order_reflections_are_deterministic(self):
        result = self.sense([event()])
        echoes = result["events"][0]["echoes"]
        self.assertEqual({item["wall"] for item in echoes},
                         {"left", "right", "bottom", "top"})
        self.assertEqual(
            next(item["path_length_mm"] for item in echoes
                 if item["wall"] == "bottom"),
            7000,
        )
        self.assertEqual(self.sense([event()]), result)

    def test_reflection_path_length_is_sum_of_both_legs(self):
        item = next(item for item in self.sense([event()])["events"][0]["echoes"]
                    if item["wall"] == "bottom")
        self.assertEqual(item["bounce_mm"], [5000, 0])
        self.assertEqual(item["path_length_mm"], 7000)
        self.assertEqual(item["received_level_milli"], 125)

    def test_range_gate_omits_long_echoes(self):
        result = self.sense([event()], max_range_mm=6000)
        self.assertEqual(result["events"][0]["echoes"], [])

    def test_obstacle_blocks_reflection_path(self):
        room = Room(10.0, 10.0, ((4.5, 1.0, 1.0, 3.0),))
        state = initial_state(room, 5.0, 5.0)
        result = observe_first_order_echoes(
            room, state, [event()], expected_revision=state.revision,
        )
        walls = {item["wall"] for item in result["events"][0]["echoes"]}
        self.assertNotIn("bottom", walls)

    def test_event_order_does_not_change_output(self):
        first, second = event("a"), event("b", x=6.0)
        self.assertEqual(self.sense([first, second]),
                         self.sense([second, first]))

    def test_duplicate_ids_rejected(self):
        with self.assertRaisesRegex(EchoError, "duplicate"):
            self.sense([event("same"), event("same")])

    def test_stale_revision_rejected(self):
        with self.assertRaisesRegex(EchoError, "revision"):
            observe_first_order_echoes(
                self.room, self.state, [], expected_revision="0" * 64,
            )

    def test_future_and_expired_events_rejected(self):
        next_state, _ = advance(
            self.room, self.state, tick=1, dx=0.1, dy=0.0,
            expected_revision=self.state.revision,
        )
        with self.assertRaises(EchoError):
            observe_first_order_echoes(
                self.room, next_state, [event(tick=2)],
                expected_revision=next_state.revision,
            )
        with self.assertRaises(EchoError):
            observe_first_order_echoes(
                self.room, next_state, [event(tick=0)],
                expected_revision=next_state.revision, max_age_ticks=0,
            )

    def test_strict_shapes_and_bool_as_int_rejected(self):
        for bad in (
            event(tick=True), event(level=True), {**event(), "extra": 1},
            [], None,
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(EchoError):
                    self.sense([bad])

    def test_invalid_utf8_and_out_of_room_sources_rejected(self):
        for bad in (event("\ud800"), event(x=-1.0)):
            with self.subTest(bad=bad):
                with self.assertRaises(EchoError):
                    self.sense([bad])

    def test_budgets_are_bounded_and_strict(self):
        with self.assertRaises(EchoError):
            self.sense([event(str(i)) for i in range(17)])
        for kwargs in ({"max_range_mm": True}, {"max_range_mm": 0},
                       {"max_age_ticks": -1}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(EchoError):
                    self.sense([], **kwargs)

    def test_input_is_not_mutated(self):
        events = [event()]
        before = copy.deepcopy(events)
        self.sense(events)
        self.assertEqual(events, before)

    def test_constructor_bypassed_room_mutation_rejected(self):
        room = Room(10.0, 10.0)
        object.__setattr__(room, "width", float("nan"))
        with self.assertRaises(EchoError):
            observe_first_order_echoes(
                room, self.state, [], expected_revision=self.state.revision,
            )


if __name__ == "__main__":
    unittest.main()
