import copy
import unittest
from mojum.world_room import Room, initial_state, advance
from mojum.world_acoustics import AcousticError, EVENT_SCHEMA, OBSERVATION_SCHEMA, sense_sound_events

def event(event_id="near", *, tick=0, x=2.0, y=1.0, level=1000):
    return {"schema": EVENT_SCHEMA, "event_id": event_id, "source_tick": tick,
            "x": x, "y": y, "source_level_milli": level}

class WorldAcousticTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(10.0, 10.0)
        self.state = initial_state(self.room, 1.0, 1.0)

    def sense(self, events, **kwargs):
        return sense_sound_events(self.room, self.state, events,
                                  expected_revision=self.state.revision, **kwargs)

    def test_schema_and_empty_observation(self):
        result = self.sense([])
        self.assertEqual(result["schema"], OBSERVATION_SCHEMA)
        self.assertEqual(result["events"], [])
        self.assertEqual(len(result["observation_sha256"]), 64)

    def test_distance_and_integer_attenuation(self):
        item = self.sense([event()])["events"][0]
        self.assertEqual(item["distance_mm"], 1000)
        self.assertEqual(item["received_level_milli"], 500)
        self.assertTrue(item["audible"])

    def test_attenuation_decreases_with_distance(self):
        near = self.sense([event("near", x=2.0)])["events"][0]
        far = self.sense([event("far", x=4.0)])["events"][0]
        self.assertGreater(near["received_level_milli"], far["received_level_milli"])

    def test_input_order_does_not_change_output(self):
        a, b = event("a", x=2.0), event("b", x=3.0)
        self.assertEqual(self.sense([a, b]), self.sense([b, a]))

    def test_stable_result_order(self):
        result = self.sense([event("z"), event("b"), event("far", x=4.0)])
        self.assertEqual([x["event_id"] for x in result["events"]], ["b", "z", "far"])

    def test_obstacle_occludes_event(self):
        room = Room(10.0, 10.0, ((2.0, 0.5, 0.2, 1.0),))
        state = initial_state(room, 1.0, 1.0)
        item = sense_sound_events(room, state, [event("behind", x=3.0)],
                                  expected_revision=state.revision)["events"][0]
        self.assertTrue(item["occluded"])
        self.assertFalse(item["audible"])
        self.assertEqual(item["received_level_milli"], 0)

    def test_no_obstacle_does_not_occlude(self):
        self.assertFalse(self.sense([event()])["events"][0]["occluded"])

    def test_range_and_threshold_gates(self):
        far = self.sense([event(x=5.0)], max_range_mm=1000)["events"][0]
        self.assertEqual(far["received_level_milli"], 0)
        self.assertFalse(far["audible"])
        quiet = self.sense([event(level=1)], threshold_milli=2)["events"][0]
        self.assertFalse(quiet["audible"])

    def test_age_report_and_expiry(self):
        state, _ = advance(self.room, self.state, tick=1, dx=0.1, dy=0.0,
                           expected_revision=self.state.revision)
        result = sense_sound_events(self.room, state, [event(tick=0)],
                                    expected_revision=state.revision, max_age_ticks=1)
        self.assertEqual(result["events"][0]["age_ticks"], 1)
        with self.assertRaises(AcousticError):
            sense_sound_events(self.room, state, [event(tick=0)],
                               expected_revision=state.revision, max_age_ticks=0)

    def test_future_event_rejected(self):
        state, _ = advance(self.room, self.state, tick=1, dx=0.1, dy=0.0,
                           expected_revision=self.state.revision)
        with self.assertRaises(AcousticError):
            sense_sound_events(self.room, state, [event(tick=2)],
                               expected_revision=state.revision)

    def test_stale_revision_rejected(self):
        with self.assertRaisesRegex(AcousticError, "revision"):
            sense_sound_events(self.room, self.state, [], expected_revision="0" * 64)

    def test_constructor_bypassed_room_mutation_rejected(self):
        room = Room(10.0, 10.0)
        object.__setattr__(room, "width", float("nan"))
        with self.assertRaises(AcousticError):
            sense_sound_events(room, self.state, [], expected_revision=self.state.revision)

    def test_constructor_bypassed_state_mutation_rejected(self):
        state = initial_state(self.room, 1.0, 1.0)
        object.__setattr__(state, "x", 500.0)
        with self.assertRaises(AcousticError):
            sense_sound_events(self.room, state, [], expected_revision=state.revision)

    def test_strict_event_shape_and_native_types(self):
        bads = [event(level=True), event(tick=True), event(x=True),
                {**event(), "extra": 1}, [], None]
        for bad in bads:
            with self.subTest(kind=type(bad).__name__):
                with self.assertRaises(AcousticError):
                    self.sense([bad])

    def test_invalid_utf8_id_and_duplicate_ids_rejected(self):
        with self.assertRaises(AcousticError):
            self.sense([event("\ud800")])
        with self.assertRaisesRegex(AcousticError, "duplicate"):
            self.sense([event("same"), event("same")])

    def test_invalid_source_coordinates_rejected(self):
        for x, y in ((-1.0, 1.0), (11.0, 1.0)):
            with self.subTest(x=x, y=y):
                with self.assertRaises(AcousticError):
                    self.sense([event(x=x, y=y)])

    def test_co_located_source_is_valid(self):
        item = self.sense([event(x=1.0, y=1.0)])["events"][0]
        self.assertEqual(item["distance_mm"], 0)
        self.assertTrue(item["audible"])

    def test_budgets_are_strict_and_bounded(self):
        with self.assertRaises(AcousticError):
            self.sense([event(str(i)) for i in range(33)])
        for kwargs in ({"max_range_mm": True}, {"max_range_mm": 0},
                       {"threshold_milli": False}, {"max_age_ticks": -1}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(AcousticError):
                    self.sense([], **kwargs)

    def test_rejects_listener_coordinate_overflow_before_rounding(self):
        room = Room(1e308, 1e308)
        state = initial_state(room, 1e308, 1e308)
        with self.assertRaisesRegex(AcousticError, "millimetre coordinate"):
            sense_sound_events(room, state, [], expected_revision=state.revision)

    def test_rejects_event_coordinate_overflow_before_rounding(self):
        room = Room(1e308, 1e308)
        state = initial_state(room, 1.0, 1.0)
        with self.assertRaisesRegex(AcousticError, "millimetre coordinate"):
            sense_sound_events(
                room, state, [event("overflow", x=1e308, y=1.0)],
                expected_revision=state.revision,
            )

    def test_oversized_event_mapping_rejected_before_key_enumeration(self):
        raw = event()
        raw.update({f"extra_{index}": 0 for index in range(25_000)})
        with self.assertRaisesRegex(AcousticError, "exact keys"):
            self.sense([raw])

    def test_inputs_are_not_mutated(self):
        events = [event()]
        before = copy.deepcopy(events)
        self.sense(events)
        self.assertEqual(events, before)
        self.assertEqual(self.state, initial_state(self.room, 1.0, 1.0))

if __name__ == "__main__":
    unittest.main()
