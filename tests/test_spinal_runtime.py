"""Independent acceptance for the bounded, opt-in Brain–Spine runtime.

These tests drive public inputs, logical time, receipts and JSON checkpoints;
they do not access the runtime's queues or scheduling implementation.
"""
import copy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import unittest

from mojum.motor_gate import MotorGate
from mojum.spinal_graph import SpinalError, build_spinal_graph
from mojum.spinal_runtime import (
    DEFAULT_RATES, SensoryFrame, SpinalIntent, SpinalRuntime,
)


PHASES = ("sensory", "motor", "cpg", "coordination", "intention",
          "homeostasis", "cognition")
FAST_RATES = {phase: 20 for phase in PHASES}


def fixture(**options):
    """Two independently bound joints; every phase runs every six ticks."""
    revision = options.pop("revision", 3)
    limits = options.pop("limits", {"hip": (-1.0, 1.0), "knee": (-2.0, 2.0)})
    bindings = options.pop("bindings", {"hip": "L1", "knee": "L2"})
    gate = MotorGate(limits, revision=revision)
    settings = {"base_hz": 120, "rates": dict(FAST_RATES), **options}
    return gate, SpinalRuntime(gate, bindings, **settings)


def advance(runtime, ticks):
    return [runtime.step() for _ in range(ticks)]


def rehash(snapshot):
    """Recompute the public checksum to exercise semantic validation too."""
    body = {key: value for key, value in snapshot.items() if key != "sha256"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    snapshot["sha256"] = hashlib.sha256(encoded).hexdigest()
    return snapshot


class SpinalRuntimeAcceptanceTests(unittest.TestCase):
    def intent(self, **changes):
        return replace(SpinalIntent("L1", "hip", 0.75, 3, 0, 30), **changes)

    def sensory(self, **changes):
        return replace(SensoryFrame("L1", "pressure", 0.5, 3, 0), **changes)

    def assert_rejected_unchanged(self, runtime, operation, error=SpinalError):
        before = runtime.snapshot()
        with self.assertRaises(error):
            operation()
        self.assertEqual(runtime.snapshot(), before)

    def test_default_rates_follow_absolute_integer_time(self):
        expected = {"sensory": 240, "motor": 120, "cpg": 100,
                    "coordination": 60, "intention": 20,
                    "homeostasis": 5, "cognition": 2}
        self.assertEqual(dict(DEFAULT_RATES), expected)
        runtime = SpinalRuntime(MotorGate({"hip": (-1, 1)}), {"hip": "L1"})
        observed = {phase: [] for phase in expected}
        for tick in range(1, 1201):
            result = runtime.step()
            self.assertEqual((result.tick, runtime.tick), (tick, tick))
            for phase in result.due:
                observed[phase].append(tick)
            self.assertEqual(
                set(result.due),
                {phase for phase, hz in expected.items() if tick % (1200 // hz) == 0},
            )
        for phase, hz in expected.items():
            self.assertEqual(observed[phase], list(range(1200 // hz, 1201, 1200 // hz)))

    def test_constructor_requires_complete_joint_bindings_and_pinned_revision(self):
        gate = MotorGate({"hip": (-1, 1), "knee": (-2, 2)}, revision=3)
        for bindings in ({}, {"hip": "L1"}, {"hip": "L1", "knee": "C9"},
                         {"hip": "L1", "knee": "L2", "ankle": "L3"},
                         [("hip", "L1")]):
            with self.subTest(bindings=bindings), self.assertRaises(SpinalError):
                SpinalRuntime(gate, bindings)
        with self.assertRaises(SpinalError):
            SpinalRuntime(object(), {"hip": "L1"})
        with self.assertRaises(SpinalError):
            SpinalRuntime(gate, {"hip": "L1", "knee": "L2"}, graph=build_spinal_graph(4))

    def test_rate_and_queue_configuration_rejects_nonintegral_or_unbounded_values(self):
        settings = (
            {"base_hz": True}, {"base_hz": 0}, {"base_hz": 12001},
            {"queue_capacity": 0}, {"queue_capacity": 257}, {"queue_capacity": True},
            {"max_intent_age_ticks": 0}, {"sensory_max_age_ticks": 0},
            {"rates": {**FAST_RATES, "motor": 7}},
            {"rates": {**FAST_RATES, "motor": 20.0}},
            {"rates": {**FAST_RATES, "motor": True}},
            {"rates": {**FAST_RATES, "motor": 121}},
            {"rates": {**FAST_RATES, "surprise": 20}},
            {"rates": {name: rate for name, rate in FAST_RATES.items() if name != "motor"}},
        )
        for options in settings:
            with self.subTest(options=options), self.assertRaises(SpinalError):
                fixture(**options)

    def test_constructor_detaches_mutable_graph_rates_bindings_and_limits(self):
        graph = build_spinal_graph(3)
        rates = dict(FAST_RATES)
        bindings = {"hip": "L1", "knee": "L2"}
        limits = {"hip": [-1.0, 1.0], "knee": [-2.0, 2.0]}
        _, runtime = fixture(graph=graph, rates=rates, bindings=bindings, limits=limits)
        before = runtime.snapshot()
        graph["edges"][0]["to"]["segment"] = "Co1"
        rates["motor"] = 1
        bindings["hip"] = "C1"
        limits["hip"][1] = 1000
        self.assertEqual(runtime.snapshot(), before)
        runtime.submit_intent(self.intent())
        result = advance(runtime, 6)[-1]
        self.assertIn("motor", result.due)
        self.assertEqual(next(item.target for item in result.motors if item.joint == "hip"), 0.75)

    def test_adjacent_routing_is_one_hop_and_separate_from_brain_mailbox(self):
        _, runtime = fixture()
        local = [self.sensory(destination="T12"), self.sensory(destination="L2"),
                 self.sensory(segment="S5", destination="Co1")]
        ascending = self.sensory(segment="C1", channel="touch", value=0.25)
        for frame in [*local, ascending]:
            runtime.submit_sensory(frame)
        self.assertEqual(runtime.read_ascending(), ())
        results = advance(runtime, 6)
        self.assertTrue(all(not result.relayed for result in results[:-1]))
        self.assertEqual(results[-1].relayed, tuple(local))
        self.assertEqual(runtime.read_ascending(), (ascending,))
        self.assertEqual(runtime.read_ascending(), ())
        self.assertTrue(all(not result.relayed for result in advance(runtime, 18)))

    def test_cross_segment_shortcuts_and_invalid_packet_fields_fail_atomically(self):
        _, runtime = fixture()
        frames = (
            self.sensory(destination="C1"), self.sensory(destination="L1"),
            self.sensory(destination="missing"), self.sensory(segment="C9"),
            self.sensory(channel="with spaces"), self.sensory(revision=2),
            self.sensory(tick=1), self.sensory(tick=True), self.sensory(value=True),
            self.sensory(value=float("nan")), self.sensory(value=float("inf")),
            self.sensory(value=10**1000), None,
        )
        for frame in frames:
            with self.subTest(kind=type(frame).__name__):
                self.assert_rejected_unchanged(runtime, lambda: runtime.submit_sensory(frame))
        intents = (
            self.intent(segment="L2"), self.intent(joint="unknown"),
            self.intent(revision=2), self.intent(revision=True), self.intent(issued_tick=1),
            self.intent(expires_tick=0), self.intent(expires_tick=601),
            self.intent(target=1.001), self.intent(target=True),
            self.intent(target=float("nan")), self.intent(target=float("inf")),
            self.intent(target=10**1000), None,
        )
        for intent in intents:
            with self.subTest(kind=type(intent).__name__):
                self.assert_rejected_unchanged(runtime, lambda: runtime.submit_intent(intent))

    def test_both_ingress_queues_reject_overflow_without_partial_changes(self):
        _, runtime = fixture(queue_capacity=2)
        first, second = self.sensory(value=0.1), self.sensory(value=0.2)
        runtime.submit_sensory(first)
        runtime.submit_sensory(second)
        self.assert_rejected_unchanged(runtime, lambda: runtime.submit_sensory(self.sensory()))
        runtime.submit_intent(self.intent())
        runtime.submit_intent(self.intent(joint="knee", segment="L2", target=-1.0))
        self.assert_rejected_unchanged(runtime, lambda: runtime.submit_intent(self.intent(target=0.0)))
        self.assertEqual(runtime.pending_counts, {"sensory": 2, "intentions": 2,
                                                   "ascending": 0, "active": 0})
        result = advance(runtime, 6)[-1]
        self.assertEqual(runtime.read_ascending(), (first, second))
        self.assertEqual({item.joint: item.target for item in result.motors},
                         {"hip": 0.75, "knee": -1.0})

    def test_paused_cognitive_consumer_drops_oldest_mail_but_spinal_expiry_continues(self):
        _, runtime = fixture(queue_capacity=2)
        runtime.submit_intent(self.intent(expires_tick=9))
        results = []
        sent = []
        for value in range(5):
            frame = self.sensory(tick=runtime.tick, value=value)
            sent.append(frame)
            runtime.submit_sensory(frame)
            results.extend(advance(runtime, 6))
            self.assertLessEqual(runtime.pending_counts["ascending"], 2)
        self.assertEqual(runtime.tick, 30)
        self.assertEqual(sum(result.dropped_ascending for result in results), 3)
        self.assertEqual(runtime.read_ascending(), tuple(sent[-2:]))
        at_expiry = next(result for result in results if result.tick == 9)
        self.assertNotIn("motor", at_expiry.due)
        self.assertEqual(at_expiry.expired_intents, 1)
        self.assertTrue(all(item.mode == "rest" and item.target is None for item in at_expiry.motors))
        self.assertTrue(all(item.target is None for result in results if result.tick >= 9
                            for item in result.motors))

    def test_ascending_reads_are_bounded_ordered_and_do_not_mutate_on_bad_limit(self):
        _, runtime = fixture(queue_capacity=3)
        frames = tuple(self.sensory(value=index) for index in range(3))
        for frame in frames:
            runtime.submit_sensory(frame)
        advance(runtime, 6)
        self.assertEqual(runtime.read_ascending(0), ())
        for limit in (-1, 4, True, 1.0):
            self.assert_rejected_unchanged(runtime, lambda: runtime.read_ascending(limit))
        self.assertEqual(runtime.read_ascending(1), frames[:1])
        self.assertEqual(runtime.read_ascending(2), frames[1:])
        self.assertEqual(runtime.pending_counts["ascending"], 0)

    def test_sensory_age_is_enforced_before_delivery_and_in_unread_mailbox(self):
        _, queued = fixture(sensory_max_age_ticks=2)
        queued.submit_sensory(self.sensory())
        results = advance(queued, 6)
        self.assertEqual(sum(result.dropped_sensory for result in results), 1)
        self.assertEqual(queued.read_ascending(), ())
        self.assertEqual(queued.pending_counts["sensory"], 0)
        self.assert_rejected_unchanged(queued, lambda: queued.submit_sensory(self.sensory()))
        _, mailbox = fixture(sensory_max_age_ticks=7)
        mailbox.submit_sensory(self.sensory())
        advance(mailbox, 7)
        self.assertEqual(mailbox.pending_counts["ascending"], 1)
        result = mailbox.step()
        self.assertEqual(result.dropped_sensory, 1)
        self.assertEqual(mailbox.read_ascending(), ())

    def test_intent_expiring_on_first_dispatch_never_becomes_active(self):
        _, runtime = fixture()
        runtime.submit_intent(self.intent(expires_tick=6))
        results = advance(runtime, 6)
        self.assertEqual(results[-1].expired_intents, 1)
        self.assertTrue(all(item.target is None for result in results for item in result.motors))
        self.assertEqual(runtime.pending_counts["active"], 0)
        self.assert_rejected_unchanged(runtime, lambda: runtime.submit_intent(self.intent(expires_tick=6)))

    def test_active_intent_rest_is_immediate_at_expiry_between_motor_boundaries(self):
        _, runtime = fixture()
        runtime.submit_intent(self.intent(expires_tick=8))
        first = advance(runtime, 6)[-1]
        hip = next(item for item in first.motors if item.joint == "hip")
        self.assertEqual((hip.mode, hip.target, hip.expires_tick), ("intent", 0.75, 8))
        self.assertEqual(runtime.step().motors, ())
        expiry = runtime.step()
        self.assertEqual(expiry.tick, 8)
        self.assertNotIn("motor", expiry.due)
        self.assertEqual(expiry.expired_intents, 1)
        self.assertTrue(all(item.mode == "rest" and item.target is None for item in expiry.motors))
        self.assertEqual(runtime.pending_counts["active"], 0)

    def test_newest_intention_wins_but_older_arrival_cannot_replace_it(self):
        _, runtime = fixture()
        runtime.submit_intent(self.intent(target=0.25))
        advance(runtime, 2)
        runtime.submit_intent(self.intent(issued_tick=2, target=-0.5))
        self.assert_rejected_unchanged(runtime,
            lambda: runtime.submit_intent(self.intent(issued_tick=1, target=1.0)))
        selected = advance(runtime, 4)[-1]
        self.assertEqual(next(item.target for item in selected.motors if item.joint == "hip"), -0.5)
        self.assert_rejected_unchanged(runtime,
            lambda: runtime.submit_intent(self.intent(issued_tick=1, target=1.0)))

    def test_estop_after_ingress_blocks_emission_and_clears_motor_work(self):
        gate, runtime = fixture()
        runtime.submit_intent(self.intent())
        gate.trip_estop()
        result = runtime.step()
        self.assertEqual(result.inhibit_reason, "estop")
        self.assertTrue(all(item.mode == "inhibited" and item.target is None for item in result.motors))
        self.assertEqual((runtime.pending_counts["intentions"], runtime.pending_counts["active"]), (0, 0))
        self.assert_rejected_unchanged(runtime, lambda: runtime.submit_intent(self.intent()), PermissionError)
        self.assertTrue(all(item.target is None for result in advance(runtime, 12) for item in result.motors))

    def test_estop_also_revokes_already_active_intention_between_boundaries(self):
        gate, runtime = fixture()
        runtime.submit_intent(self.intent())
        advance(runtime, 6)
        gate.trip_estop()
        result = runtime.step()
        self.assertNotIn("motor", result.due)
        self.assertEqual(result.inhibit_reason, "estop")
        self.assertTrue(all(item.target is None for item in result.motors))
        self.assertEqual(runtime.pending_counts["active"], 0)

    def test_gate_revision_drift_inhibits_targets_and_refuses_checkpoint_use(self):
        gate, runtime = fixture()
        runtime.submit_intent(self.intent())
        checkpoint = runtime.snapshot()
        gate.restore_revision(4)
        result = runtime.step()
        self.assertEqual(result.inhibit_reason, "revision_changed")
        self.assertTrue(all(item.target is None for item in result.motors))
        with self.assertRaises(SpinalError):
            runtime.snapshot()
        before = (runtime.tick, runtime.pending_counts)
        with self.assertRaises(SpinalError):
            runtime.restore(checkpoint)
        self.assertEqual((runtime.tick, runtime.pending_counts), before)

    def test_result_messages_and_public_counters_cannot_modify_runtime(self):
        _, runtime = fixture()
        runtime.submit_intent(self.intent())
        runtime.submit_sensory(self.sensory(destination="L2"))
        result = advance(runtime, 6)[-1]
        before = runtime.snapshot()
        counts = runtime.pending_counts
        counts["active"] = 0
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            result.motors[0].target = 1000
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            result.relayed[0].destination = "Co1"
        with self.assertRaises(AttributeError):
            runtime.tick = 0
        self.assertEqual(runtime.snapshot(), before)

    def test_cold_checkpoint_continuation_matches_uninterrupted_and_replayed_inputs(self):
        _, continuous = fixture(queue_capacity=4)
        _, replayed = fixture(queue_capacity=4)
        for runtime in (continuous, replayed):
            runtime.submit_intent(self.intent(expires_tick=17))
            runtime.submit_sensory(self.sensory(value=0.1))
            advance(runtime, 7)
            runtime.submit_sensory(self.sensory(tick=7, value=0.2))
            runtime.submit_intent(self.intent(joint="knee", segment="L2", target=-1,
                                              issued_tick=7, expires_tick=23))
        self.assertEqual(continuous.snapshot(), replayed.snapshot())
        saved = json.loads(json.dumps(continuous.snapshot()))
        _, resumed = fixture(queue_capacity=4)
        resumed.restore(saved)
        self.assertEqual(resumed.snapshot(), continuous.snapshot())
        saved["sensory"][0]["value"] = 999
        for target_tick in range(8, 31):
            outputs = [runtime.step() for runtime in (continuous, replayed, resumed)]
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[0], outputs[2])
            self.assertEqual(outputs[0].tick, target_tick)
            if target_tick in (12, 18, 24):
                mail = [runtime.read_ascending(1) for runtime in (continuous, replayed, resumed)]
                self.assertEqual(mail[0], mail[1])
                self.assertEqual(mail[0], mail[2])
        self.assertEqual(continuous.snapshot(), resumed.snapshot())
        self.assertEqual(continuous.snapshot(), replayed.snapshot())

    def test_checkpoint_profile_pins_graph_joint_bindings_limits_and_scheduler(self):
        _, source = fixture()
        checkpoint = source.snapshot()
        variants = (
            {"revision": 4}, {"bindings": {"hip": "L2", "knee": "L1"}},
            {"limits": {"hip": (-2, 2), "knee": (-2, 2)}},
            {"base_hz": 240}, {"rates": {**FAST_RATES, "motor": 10}},
            {"queue_capacity": 2}, {"max_intent_age_ticks": 300},
            {"sensory_max_age_ticks": 600},
        )
        for options in variants:
            _, target = fixture(**options)
            with self.subTest(options=options):
                self.assertNotEqual(target.profile_sha256, source.profile_sha256)
                self.assert_rejected_unchanged(target, lambda: target.restore(checkpoint))
        graph = build_spinal_graph(3)
        graph["segments"].reverse()
        graph["edges"].reverse()
        _, equivalent = fixture(graph=graph)
        self.assertEqual(equivalent.profile_sha256, source.profile_sha256)
        equivalent.restore(checkpoint)

    def test_checkpoint_checksum_and_rehashed_invalid_contents_fail_atomically(self):
        gate, runtime = fixture(queue_capacity=2)
        runtime.submit_intent(self.intent())
        runtime.submit_sensory(self.sensory())
        advance(runtime, 6)
        runtime.submit_sensory(self.sensory(tick=6, value=0.2))
        runtime.submit_intent(self.intent(joint="knee", segment="L2", issued_tick=6))
        good = runtime.snapshot()
        damaged = copy.deepcopy(good)
        damaged["dropped_sensory"] += 1
        self.assert_rejected_unchanged(runtime, lambda: runtime.restore(damaged))
        mutations = (
            lambda s: s.__setitem__("tick", True),
            lambda s: s.__setitem__("tick", -1),
            lambda s: s.__setitem__("tick", 2**53),
            lambda s: s.__setitem__("dropped_sensory", -1),
            lambda s: s.__setitem__("dropped_ascending", True),
            lambda s: s.__setitem__("estop_latched", 1),
            lambda s: s.__setitem__("profile_sha256", "0" * 64),
            lambda s: s.__setitem__("schema", "mojum.sim-checkpoint/0.1"),
            lambda s: s["sensory"].extend(copy.deepcopy(s["sensory"]) * 2),
            lambda s: s["sensory"][0].__setitem__("destination", "C1"),
            lambda s: s["sensory"][0].__setitem__("tick", 7),
            lambda s: s["sensory"][0].__setitem__("value", True),
            lambda s: s["ascending"][0].__setitem__("destination", "L2"),
            lambda s: s["intentions"][0].__setitem__("expires_tick", 6),
            lambda s: s["intentions"][0].__setitem__("issued_tick", 7),
            lambda s: s["intentions"][0].__setitem__("segment", "L1"),
            lambda s: s["active"][0].__setitem__("target", 2.0),
            lambda s: s["active"].append(copy.deepcopy(s["active"][0])),
        )
        for index, mutate in enumerate(mutations):
            damaged = copy.deepcopy(good)
            mutate(damaged)
            rehash(damaged)
            with self.subTest(index=index):
                self.assert_rejected_unchanged(runtime, lambda: runtime.restore(damaged))
                self.assertFalse(gate.estop_latched)

    def test_restore_rejects_impossible_dispatched_state_before_phase_boundary(self):
        _, source = fixture()
        source.submit_intent(self.intent())
        source.submit_sensory(self.sensory())
        advance(source, 6)
        saved = source.snapshot()
        corruptions = []
        before_any_dispatch = copy.deepcopy(saved)
        before_any_dispatch["tick"] = 0
        corruptions.append(before_any_dispatch)
        active_issued_after_dispatch = copy.deepcopy(saved)
        active_issued_after_dispatch["active"][0]["issued_tick"] = 6
        corruptions.append(active_issued_after_dispatch)
        sensory_recorded_after_dispatch = copy.deepcopy(saved)
        sensory_recorded_after_dispatch["ascending"][0]["tick"] = 6
        corruptions.append(sensory_recorded_after_dispatch)
        for index, corrupted in enumerate(corruptions):
            _, target = fixture()
            with self.subTest(index=index):
                self.assert_rejected_unchanged(target, lambda: target.restore(rehash(corrupted)))

    def test_restore_rejects_backward_time_and_never_clears_process_estop(self):
        gate, source = fixture()
        old = source.snapshot()
        source.submit_intent(self.intent())
        advance(source, 6)
        self.assert_rejected_unchanged(source, lambda: source.restore(old))
        saved = source.snapshot()
        resumed_gate, resumed = fixture()
        resumed_gate.trip_estop()
        resumed.restore(saved)
        self.assertTrue(resumed_gate.estop_latched)
        result = resumed.step()
        self.assertEqual(result.inhibit_reason, "estop")
        self.assertTrue(all(item.target is None for item in result.motors))
        gate.trip_estop()
        stopped = source.snapshot()
        cold_gate, cold = fixture()
        cold.restore(stopped)
        self.assertTrue(cold_gate.estop_latched)
        self.assertEqual(cold.step().inhibit_reason, "estop")

    def test_invalid_checkpoint_cannot_partially_trip_gate(self):
        gate, runtime = fixture()
        invalid = runtime.snapshot()
        invalid["estop_latched"] = True
        invalid["sha256"] = "0" * 64
        self.assert_rejected_unchanged(runtime, lambda: runtime.restore(invalid))
        self.assertFalse(gate.estop_latched)

    def test_hostile_checkpoint_containers_and_scalars_are_rejected_without_hooks(self):
        class HostileDict(dict):
            def __len__(self):
                raise AssertionError("untrusted mapping hook")

            def __iter__(self):
                raise AssertionError("untrusted mapping hook")

        class HostileList(list):
            def __len__(self):
                raise AssertionError("untrusted sequence hook")

        class HostileInt(int):
            def __le__(self, other):
                raise AssertionError("untrusted scalar hook")

        class HostileString(str):
            def __eq__(self, other):
                raise AssertionError("untrusted string hook")

        _, runtime = fixture()
        self.assert_rejected_unchanged(runtime, lambda: runtime.restore(HostileDict()))
        for key, value in (("tick", HostileInt(0)), ("sensory", HostileList()),
                           ("schema", HostileString("mojum.spinal-state/1")),
                           ("profile_sha256", HostileString(runtime.profile_sha256))):
            snapshot = runtime.snapshot()
            snapshot[key] = value
            with self.subTest(key=key):
                self.assert_rejected_unchanged(runtime, lambda: runtime.restore(snapshot))
        cyclic = runtime.snapshot()
        cyclic["sensory"] = [cyclic]
        self.assert_rejected_unchanged(runtime, lambda: runtime.restore(cyclic))


if __name__ == "__main__":
    unittest.main()
