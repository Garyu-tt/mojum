import dataclasses
import unittest

from mojum.spinal_conduction import (
    DELIVERY_SCHEMA,
    FRAME_SCHEMA,
    MAX_PENDING_SIGNALS,
    MAX_REVISION,
    MAX_ROUTE_DELAY_TICKS,
    MAX_ROUTES,
    MAX_SIGNALS_PER_TICK,
    MAX_SIGNAL_MILLI,
    MAX_TICK,
    PROFILE_SCHEMA,
    SIGNAL_SCHEMA,
    ConductionProfile,
    ConductionRoute,
    PendingSignal,
    SpinalConductionError,
    SpinalConductionQueue,
    compile_conduction_profile,
)


def profile_spec(routes=None, **changes):
    value = {
        "schema": PROFILE_SCHEMA,
        "visibility": "PUBLIC_SYNTHETIC",
        "consent": "explicit",
        "revision": 7,
        "max_pending": 16,
        "routes": [
            {"route_id": "route.fast", "delay_ticks": 1},
            {"route_id": "route.slow", "delay_ticks": 3},
        ] if routes is None else routes,
    }
    value.update(changes)
    return value


def signal(event_id, route_id="route.fast", **changes):
    value = {
        "schema": SIGNAL_SCHEMA,
        "visibility": "PUBLIC_SYNTHETIC",
        "consent": "explicit",
        "profile_revision": 7,
        "event_id": event_id,
        "source_id": "synthetic-fixture",
        "source_revision": 2,
        "route_id": route_id,
        "signal_milli": 125,
    }
    value.update(changes)
    return value


class ProfileAdmissionTests(unittest.TestCase):
    def test_profile_is_canonical_detached_and_deterministic(self):
        supplied = profile_spec(routes=[
            {"route_id": "route.slow", "delay_ticks": 3},
            {"route_id": "route.fast", "delay_ticks": 1},
        ])
        first = compile_conduction_profile(supplied)
        second = compile_conduction_profile(profile_spec())

        self.assertEqual(first, second)
        self.assertEqual(
            tuple(route.route_id for route in first.routes),
            ("route.fast", "route.slow"),
        )
        self.assertEqual(len(first.sha256), 64)
        supplied["routes"][0]["route_id"] = "mutated"
        self.assertEqual(first.routes[1].route_id, "route.slow")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            first.revision = 9

    def test_profile_rejects_shapes_native_types_duplicates_and_names(self):
        invalid = (
            profile_spec(revision=True),
            profile_spec(max_pending=0),
            profile_spec(max_pending=MAX_PENDING_SIGNALS + 1),
            profile_spec(routes=[]),
            profile_spec(routes=[{"route_id": "bad route", "delay_ticks": 1}]),
            profile_spec(routes=[{"route_id": "r", "delay_ticks": True}]),
            profile_spec(routes=[{"route_id": "r", "delay_ticks": -1}]),
            profile_spec(routes=[
                {"route_id": "same", "delay_ticks": 1},
                {"route_id": "same", "delay_ticks": 2},
            ]),
            dict(profile_spec(), runtime="spinal"),
            dict(profile_spec(), visibility="PRIVATE"),
            dict(profile_spec(), consent="implicit"),
        )
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(SpinalConductionError):
                compile_conduction_profile(item)

        class DictSubclass(dict):
            pass

        class ListSubclass(list):
            pass

        with self.assertRaises(SpinalConductionError):
            compile_conduction_profile(DictSubclass(profile_spec()))
        with self.assertRaises(SpinalConductionError):
            compile_conduction_profile(profile_spec(routes=ListSubclass(profile_spec()["routes"])))
        with self.assertRaises(SpinalConductionError):
            compile_conduction_profile(profile_spec(routes=[DictSubclass({
                "route_id": "r", "delay_ticks": 1,
            })]))
        oversized = profile_spec()
        oversized.update({f"extra_{index}": index for index in range(10_000)})
        with self.assertRaises(SpinalConductionError):
            compile_conduction_profile(oversized)

    def test_profile_envelope_cannot_be_spoofed_by_equality_or_string_subclasses(self):
        class EqualToEverything:
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        class StringSubclass(str):
            pass

        for field, value in (
            ("schema", EqualToEverything()),
            ("visibility", EqualToEverything()),
            ("consent", EqualToEverything()),
            ("schema", StringSubclass(PROFILE_SCHEMA)),
            ("visibility", StringSubclass("PUBLIC_SYNTHETIC")),
            ("consent", StringSubclass("explicit")),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(
                SpinalConductionError
            ):
                compile_conduction_profile(profile_spec(**{field: value}))

    def test_profile_accepts_exact_route_and_delay_limits(self):
        routes = [
            {"route_id": f"r{index:02d}", "delay_ticks": MAX_ROUTE_DELAY_TICKS}
            for index in range(MAX_ROUTES)
        ]
        compiled = compile_conduction_profile(
            profile_spec(routes=routes, revision=MAX_REVISION, max_pending=MAX_PENDING_SIGNALS)
        )
        self.assertEqual(len(compiled.routes), MAX_ROUTES)
        self.assertEqual(compiled.routes[-1].delay_ticks, MAX_ROUTE_DELAY_TICKS)
        with self.assertRaises(SpinalConductionError):
            compile_conduction_profile(profile_spec(routes=routes + [
                {"route_id": "overflow", "delay_ticks": 0}
            ]))

    def test_forged_profile_cannot_initialize_a_queue(self):
        valid = compile_conduction_profile(profile_spec())
        forged = dataclasses.replace(valid, max_pending=1)
        with self.assertRaisesRegex(SpinalConductionError, "digest"):
            SpinalConductionQueue(forged)
        with self.assertRaises(SpinalConductionError):
            SpinalConductionQueue(dataclasses.replace(
                valid,
                routes=(ConductionRoute("route.slow", 3), ConductionRoute("route.fast", 1)),
            ))

        class StringSubclass(str):
            pass

        for field, value in (
            ("schema", StringSubclass(PROFILE_SCHEMA)),
            ("visibility", StringSubclass("PUBLIC_SYNTHETIC")),
            ("consent", StringSubclass("explicit")),
        ):
            with self.subTest(field=field), self.assertRaises(SpinalConductionError):
                SpinalConductionQueue(dataclasses.replace(valid, **{field: value}))


class QueueTransitionTests(unittest.TestCase):
    def test_delay_zero_and_canonical_delivery_order(self):
        profile = compile_conduction_profile(profile_spec(routes=[
            {"route_id": "route.z", "delay_ticks": 1},
            {"route_id": "route.zero", "delay_ticks": 0},
            {"route_id": "route.a", "delay_ticks": 1},
        ]))
        queue = SpinalConductionQueue(profile)
        first = queue.step(1, [
            signal("event-z", "route.z"),
            signal("event-zero", "route.zero"),
            signal("event-a", "route.a"),
        ])
        self.assertEqual(first.schema, FRAME_SCHEMA)
        self.assertEqual([item.event_id for item in first.deliveries], ["event-zero"])
        self.assertEqual(first.deliveries[0].schema, DELIVERY_SCHEMA)
        self.assertEqual(first.deliveries[0].visibility, "PUBLIC_SYNTHETIC")
        self.assertEqual(first.deliveries[0].consent, "explicit")
        self.assertEqual(first.deliveries[0].delivered_tick, 1)
        self.assertEqual(first.pending_count, 2)
        self.assertFalse(first.physical_actuation)

        second = queue.step(2, [])
        self.assertEqual(
            [item.event_id for item in second.deliveries],
            ["event-a", "event-z"],
        )
        self.assertTrue(all(item.delivered_tick == 2 for item in second.deliveries))
        self.assertEqual(queue.pending_count, 0)

    def test_input_order_does_not_change_frames_or_receipts(self):
        profile = compile_conduction_profile(profile_spec(routes=[
            {"route_id": "a", "delay_ticks": 2},
            {"route_id": "z", "delay_ticks": 2},
        ]))
        left = SpinalConductionQueue(profile)
        right = SpinalConductionQueue(profile)
        batch = [signal("e-z", "z"), signal("e-a", "a")]
        self.assertEqual(left.step(1, batch), right.step(1, list(reversed(batch))))
        self.assertEqual(left.step(2, []), right.step(2, []))
        self.assertEqual(left.step(3, []), right.step(3, []))
        self.assertEqual(left.snapshot(), right.snapshot())

    def test_invalid_signal_or_tick_fails_without_state_change(self):
        queue = SpinalConductionQueue(compile_conduction_profile(profile_spec()))
        initial = queue.snapshot()
        invalid = (
            signal("e", "unknown"),
            signal("e", source_revision=True),
            signal("e", profile_revision=True),
            signal("e", profile_revision=8),
            signal("e", signal_milli=MAX_SIGNAL_MILLI + 1),
            signal("e", visibility="PRIVATE"),
            signal("e", consent="implicit"),
            dict(signal("e"), payload="untrusted"),
        )
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(SpinalConductionError):
                queue.step(1, [item])
            self.assertEqual(queue.snapshot(), initial)
        for tick in (0, 2, True):
            with self.subTest(tick=tick), self.assertRaises(SpinalConductionError):
                queue.step(tick, [])
            self.assertEqual(queue.snapshot(), initial)
        with self.assertRaises(SpinalConductionError):
            queue.step(1, tuple())
        self.assertEqual(queue.snapshot(), initial)

    def test_signal_envelope_cannot_be_spoofed_by_equality_or_string_subclasses(self):
        class EqualToEverything:
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        class StringSubclass(str):
            pass

        queue = SpinalConductionQueue(compile_conduction_profile(profile_spec()))
        initial = queue.snapshot()
        for field, value in (
            ("schema", EqualToEverything()),
            ("visibility", EqualToEverything()),
            ("consent", EqualToEverything()),
            ("schema", StringSubclass(SIGNAL_SCHEMA)),
            ("visibility", StringSubclass("PUBLIC_SYNTHETIC")),
            ("consent", StringSubclass("explicit")),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(
                SpinalConductionError
            ):
                queue.step(1, [signal("spoof", **{field: value})])
            self.assertEqual(queue.snapshot(), initial)

    def test_duplicate_pending_or_batch_event_id_is_rejected_atomically(self):
        queue = SpinalConductionQueue(compile_conduction_profile(profile_spec()))
        queue.step(1, [signal("pending")])
        before = queue.snapshot()
        with self.assertRaisesRegex(SpinalConductionError, "event_id"):
            queue.step(2, [signal("pending")])
        self.assertEqual(queue.snapshot(), before)
        with self.assertRaisesRegex(SpinalConductionError, "event_id"):
            queue.step(2, [signal("duplicate"), signal("duplicate", "route.slow")])
        self.assertEqual(queue.snapshot(), before)

    def test_pending_capacity_fails_closed_but_due_slots_can_be_reused(self):
        profile = compile_conduction_profile(profile_spec(
            max_pending=2,
            routes=[{"route_id": "r", "delay_ticks": 2}],
        ))
        queue = SpinalConductionQueue(profile)
        queue.step(1, [signal("one", "r"), signal("two", "r")])
        before = queue.snapshot()
        with self.assertRaisesRegex(SpinalConductionError, "capacity"):
            queue.step(2, [signal("three", "r")])
        self.assertEqual(queue.snapshot(), before)

        queue.step(2, [])
        frame = queue.step(3, [signal("three", "r"), signal("four", "r")])
        self.assertEqual([item.event_id for item in frame.deliveries], ["one", "two"])
        self.assertEqual(frame.pending_count, 2)

    def test_batch_cardinality_and_due_tick_overflow_are_bounded(self):
        queue = SpinalConductionQueue(compile_conduction_profile(profile_spec()))
        too_many = [signal(f"e{index}") for index in range(MAX_SIGNALS_PER_TICK + 1)]
        with self.assertRaises(SpinalConductionError):
            queue.step(1, too_many)
        self.assertEqual(queue.tick, 0)

        delayed = SpinalConductionQueue(compile_conduction_profile(profile_spec(
            routes=[{"route_id": "r", "delay_ticks": 2}],
        )))
        delayed._tick = MAX_TICK - 1
        before = delayed.snapshot()
        with self.assertRaisesRegex(SpinalConductionError, "due tick"):
            delayed.step(MAX_TICK, [signal("overflow", "r")])
        self.assertEqual(delayed.snapshot(), before)
        delayed._tick = MAX_TICK
        with self.assertRaisesRegex(SpinalConductionError, "lifetime"):
            delayed.step(MAX_TICK, [])

    def test_long_run_stays_bounded_and_has_no_actuator_surface(self):
        profile = compile_conduction_profile(profile_spec(
            max_pending=8,
            routes=[{"route_id": "r", "delay_ticks": 5}],
        ))
        queue = SpinalConductionQueue(profile)
        delivered = 0
        for tick in range(1, 5_001):
            frame = queue.step(tick, [signal(f"e{tick}", "r", signal_milli=(tick % 2001) - 1000)])
            delivered += len(frame.deliveries)
            self.assertLessEqual(frame.pending_count, 5)
        self.assertEqual(delivered, 4_995)
        self.assertEqual(queue.pending_count, 5)
        self.assertFalse(hasattr(queue, "actuate"))


class SnapshotTests(unittest.TestCase):
    def test_snapshot_is_immutable_bound_and_replays_identically(self):
        profile = compile_conduction_profile(profile_spec())
        first = SpinalConductionQueue(profile)
        first.step(1, [signal("slow", "route.slow")])
        first.step(2, [signal("fast", "route.fast")])
        snapshot = first.snapshot()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snapshot.tick = 9

        restored = SpinalConductionQueue.from_snapshot(profile, snapshot)
        for tick, batch in (
            (3, [signal("next-fast", "route.fast")]),
            (4, []),
            (5, [signal("next-slow", "route.slow")]),
            (6, []),
            (7, []),
            (8, []),
        ):
            self.assertEqual(first.step(tick, batch), restored.step(tick, batch))
        self.assertEqual(first.snapshot(), restored.snapshot())

    def test_restore_rejects_tampering_wrong_profile_and_noncanonical_order(self):
        profile = compile_conduction_profile(profile_spec())
        queue = SpinalConductionQueue(profile)
        queue.step(1, [signal("slow", "route.slow"), signal("fast", "route.fast")])
        snapshot = queue.snapshot()
        self.assertEqual(len(snapshot.pending), 2)
        first = snapshot.pending[0]
        tampered = (
            dataclasses.replace(snapshot, schema="future/9"),
            dataclasses.replace(snapshot, state_sha256="0" * 64),
            dataclasses.replace(snapshot, pending=tuple(reversed(snapshot.pending))),
            dataclasses.replace(
                snapshot,
                pending=(dataclasses.replace(first, due_tick=first.due_tick + 1),),
            ),
            dataclasses.replace(snapshot, pending=(dataclasses.replace(first, delay_ticks=True),)),
            dataclasses.replace(snapshot, pending=(first, dataclasses.replace(first))),
        )
        for item in tampered:
            with self.subTest(item=item), self.assertRaises(SpinalConductionError):
                SpinalConductionQueue.from_snapshot(profile, item)
        other = compile_conduction_profile(profile_spec(revision=8))
        with self.assertRaisesRegex(SpinalConductionError, "different profile"):
            SpinalConductionQueue.from_snapshot(other, snapshot)
        with self.assertRaises(SpinalConductionError):
            SpinalConductionQueue.from_snapshot(profile, dataclasses.asdict(snapshot))

        class EqualToEverything:
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        with self.assertRaises(SpinalConductionError):
            SpinalConductionQueue.from_snapshot(
                profile, dataclasses.replace(snapshot, schema=EqualToEverything())
            )

        class StringSubclass(str):
            pass

        with self.assertRaises(SpinalConductionError):
            SpinalConductionQueue.from_snapshot(
                profile,
                dataclasses.replace(snapshot, schema=StringSubclass(snapshot.schema)),
            )

    def test_restore_rejects_already_due_and_unknown_route_records(self):
        profile = compile_conduction_profile(profile_spec())
        queue = SpinalConductionQueue(profile)
        queue.step(1, [signal("slow", "route.slow")])
        snapshot = queue.snapshot()
        pending = snapshot.pending[0]
        for changed in (
            dataclasses.replace(pending, due_tick=snapshot.tick),
            dataclasses.replace(pending, route_id="unknown"),
            PendingSignal(
                event_id=pending.event_id,
                source_id=pending.source_id,
                source_revision=pending.source_revision,
                route_id=pending.route_id,
                emitted_tick=0,
                due_tick=pending.due_tick,
                delay_ticks=pending.delay_ticks,
                signal_milli=pending.signal_milli,
            ),
        ):
            forged = dataclasses.replace(snapshot, pending=(changed,))
            with self.subTest(changed=changed), self.assertRaises(SpinalConductionError):
                SpinalConductionQueue.from_snapshot(profile, forged)


if __name__ == "__main__":
    unittest.main()
