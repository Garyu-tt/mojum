import unittest

from mojum.adapter_failover import (
    SCHEMA, FailoverError, FailoverState, initial_state, replay,
    restore_state, snapshot_state, transition,
)


def event(state, event_id, tick, kind, **extra):
    return {
        "schema": SCHEMA,
        "event_id": event_id,
        "adapter_id": state.adapter_id,
        "tick": tick,
        "expected_revision": state.revision,
        "kind": kind,
        **extra,
    }


class AdapterFailoverTests(unittest.TestCase):
    def test_initial_state_uses_home_room(self):
        state = initial_state("vr-world")
        self.assertEqual(state.mode, "disabled")
        self.assertEqual(state.selected_world, "home_room")
        self.assertEqual(state.revision, 0)

    def test_enable_requires_explicit_capability_grant(self):
        state = initial_state("vr-world")
        with self.assertRaises(FailoverError):
            transition(state, event(state, "e1", 0, "enable", capability_granted=False))
        active = transition(state, event(state, "e2", 0, "enable", capability_granted=True))
        self.assertEqual(active.mode, "active")
        self.assertEqual(active.selected_world, "external_adapter")

    def test_failure_quarantines_and_selects_home_room(self):
        state = transition(initial_state("vr-world"),
                           event(initial_state("vr-world"), "enable-1", 0, "enable", capability_granted=True))
        next_state = transition(state, event(state, "failure-1", 1, "failure", error_code="timeout"))
        self.assertEqual(next_state.mode, "quarantined")
        self.assertEqual(next_state.selected_world, "home_room")
        self.assertEqual(next_state.failure_count, 1)

    def test_recovery_requires_explicit_reenable(self):
        state = initial_state("vr-world")
        state = transition(state, event(state, "enable-1", 0, "enable", capability_granted=True))
        state = transition(state, event(state, "failure-1", 1, "failure", error_code="unavailable"))
        with self.assertRaises(FailoverError):
            transition(state, event(state, "health-1", 2, "healthy"))
        resumed = transition(state, event(state, "enable-2", 2, "enable", capability_granted=True))
        self.assertEqual(resumed.mode, "active")
        self.assertEqual(resumed.failure_count, 0)

    def test_disable_always_returns_to_home_room(self):
        state = initial_state("vr-world")
        state = transition(state, event(state, "enable-1", 0, "enable", capability_granted=True))
        state = transition(state, event(state, "disable-1", 1, "disable"))
        self.assertEqual(state.mode, "disabled")
        self.assertEqual(state.selected_world, "home_room")

    def test_revision_and_tick_are_fenced(self):
        state = initial_state("vr-world")
        wrong_revision = event(state, "enable-1", 0, "enable", capability_granted=True)
        wrong_revision["expected_revision"] = 1
        with self.assertRaises(FailoverError):
            transition(state, wrong_revision)
        state = transition(state, event(state, "enable-2", 4, "enable", capability_granted=True))
        with self.assertRaises(FailoverError):
            transition(state, event(state, "disable-1", 4, "disable"))
        with self.assertRaises(FailoverError):
            transition(state, event(state, "disable-2", 3, "disable"))

    def test_boolean_is_not_an_integer_tick_or_revision(self):
        state = initial_state("vr-world")
        raw = event(state, "enable-1", True, "enable", capability_granted=True)
        with self.assertRaises(FailoverError):
            transition(state, raw)
        raw = event(state, "enable-2", 0, "enable", capability_granted=True)
        raw["expected_revision"] = False
        with self.assertRaises(FailoverError):
            transition(state, raw)

    def test_unknown_fields_and_error_codes_fail_closed(self):
        state = initial_state("vr-world")
        raw = event(state, "enable-1", 0, "enable", capability_granted=True)
        raw["debug"] = "unexpected"
        with self.assertRaises(FailoverError):
            transition(state, raw)
        active = transition(state, event(state, "enable-2", 0, "enable", capability_granted=True))
        with self.assertRaises(FailoverError):
            transition(active, event(active, "failure-1", 1, "failure", error_code="secret"))

    def test_event_adapter_identity_is_bound(self):
        state = initial_state("vr-world")
        raw = event(state, "enable-1", 0, "enable", capability_granted=True)
        raw["adapter_id"] = "other-world"
        with self.assertRaises(FailoverError):
            transition(state, raw)

    def test_duplicate_last_event_is_rejected(self):
        state = initial_state("vr-world")
        raw = event(state, "enable-1", 0, "enable", capability_granted=True)
        state = transition(state, raw)
        with self.assertRaises(FailoverError):
            transition(state, raw)

    def test_snapshot_roundtrip_and_tamper_detection(self):
        state = initial_state("vr-world")
        state = transition(state, event(state, "enable-1", 0, "enable", capability_granted=True))
        snapshot = snapshot_state(state)
        self.assertEqual(restore_state(snapshot), state)
        snapshot["mode"] = "disabled"
        with self.assertRaises(FailoverError):
            restore_state(snapshot)

    def test_replay_is_deterministic_and_does_not_mutate_events(self):
        initial = initial_state("vr-world")
        events = [
            event(initial, "enable-1", 0, "enable", capability_granted=True),
        ]
        active = transition(initial, events[0])
        events.append(event(active, "failure-1", 1, "failure", error_code="protocol_error"))
        before = [dict(item) for item in events]
        result_a = replay(initial, events)
        result_b = replay(initial, events)
        self.assertEqual(snapshot_state(result_a), snapshot_state(result_b))
        self.assertEqual(events, before)
        self.assertEqual(result_a.selected_world, "home_room")

    def test_healthy_event_only_resets_active_failure_counter(self):
        state = initial_state("vr-world")
        state = transition(state, event(state, "enable-1", 0, "enable", capability_granted=True))
        state = transition(state, event(state, "healthy-1", 1, "healthy"))
        self.assertEqual(state.mode, "active")
        self.assertEqual(state.failure_count, 0)

    def test_hostile_mapping_subclass_is_rejected_before_access(self):
        class HostileDict(dict):
            def get(self, *args, **kwargs):
                raise AssertionError("must not execute caller code")
        state = initial_state("vr-world")
        with self.assertRaises(FailoverError):
            transition(state, HostileDict())

    def test_string_subclass_event_key_is_rejected(self):
        class HostileString(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                raise AssertionError("must not compare caller subclass")
        state = initial_state("vr-world")
        raw = event(state, "enable-1", 0, "enable", capability_granted=True)
        raw[HostileString("extra")] = "x"
        with self.assertRaises(FailoverError):
            transition(state, raw)

    def test_state_must_be_exact_native_dataclass(self):
        class DerivedState(FailoverState):
            pass
        derived = DerivedState(adapter_id="vr-world")
        with self.assertRaises(FailoverError):
            transition(derived, {})

    def test_impossible_initial_active_state_is_rejected(self):
        forged = FailoverState(adapter_id="vr-world", mode="active")
        with self.assertRaises(FailoverError):
            transition(forged, {})


if __name__ == "__main__":
    unittest.main()
