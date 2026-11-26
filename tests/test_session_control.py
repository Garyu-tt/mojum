import hashlib
import json
import unittest

from mojum.session_control import (
    CREATED, PAUSED, RUNNING, TERMINATED, MAX_EVENTS,
    SessionControlError, SessionController,
)


class _HostileEquality:
    def __eq__(self, _other):
        raise AssertionError("untrusted equality must not be evaluated")


class SessionControlTests(unittest.TestCase):
    def setUp(self):
        self.session = SessionController("dante-fixture")

    def apply(self, action, event_id, tick):
        return self.session.transition(
            action,
            event_id=event_id,
            expected_revision=self.session.revision,
            committed_tick=tick,
        )

    def test_initial_snapshot_round_trip(self):
        snapshot = self.session.snapshot()
        restored = SessionController.from_snapshot(snapshot)
        self.assertEqual(restored.state, CREATED)
        self.assertEqual(restored.last_tick, 0)
        self.assertEqual(restored.revision, self.session.revision)

    def test_complete_lifecycle_round_trip(self):
        self.apply("start", "start-1", 0)
        self.apply("pause", "pause-1", 12)
        self.apply("resume", "resume-1", 12)
        self.apply("pause", "pause-2", 19)
        self.apply("terminate", "terminate-1", 19)
        snapshot = self.session.snapshot()
        restored = SessionController.from_snapshot(snapshot)
        self.assertEqual(restored.state, TERMINATED)
        self.assertEqual(restored.last_tick, 19)
        self.assertEqual(restored.snapshot(), snapshot)

    def test_stale_revision_is_rejected_without_mutation(self):
        old = self.session.revision
        self.apply("start", "start-1", 0)
        before = self.session.snapshot()
        with self.assertRaisesRegex(SessionControlError, "CAS"):
            self.session.transition("pause", event_id="pause-1",
                                    expected_revision=old, committed_tick=1)
        self.assertEqual(self.session.snapshot(), before)

    def test_tick_in_progress_is_rejected(self):
        before = self.session.snapshot()
        with self.assertRaisesRegex(SessionControlError, "between committed ticks"):
            self.session.transition("start", event_id="start-1",
                                    expected_revision=self.session.revision,
                                    committed_tick=0, tick_in_progress=True)
        self.assertEqual(self.session.snapshot(), before)

    def test_non_boolean_tick_flag_is_rejected(self):
        with self.assertRaisesRegex(SessionControlError, "boolean"):
            self.session.transition("start", event_id="start-1",
                                    expected_revision=self.session.revision,
                                    committed_tick=0, tick_in_progress=1)

    def test_tick_cannot_move_backwards(self):
        self.apply("start", "start-1", 7)
        before = self.session.snapshot()
        with self.assertRaisesRegex(SessionControlError, "backwards"):
            self.apply("pause", "pause-1", 6)
        self.assertEqual(self.session.snapshot(), before)

    def test_boolean_is_not_a_tick(self):
        with self.assertRaisesRegex(SessionControlError, "committed_tick"):
            self.apply("start", "start-1", True)
        self.assertEqual(self.session.state, CREATED)

    def test_illegal_state_transition_is_rejected(self):
        before = self.session.snapshot()
        with self.assertRaisesRegex(SessionControlError, "invalid while"):
            self.apply("resume", "resume-1", 0)
        self.assertEqual(self.session.snapshot(), before)

    def test_terminated_session_is_terminal(self):
        self.apply("terminate", "stop-1", 0)
        before = self.session.snapshot()
        with self.assertRaisesRegex(SessionControlError, "invalid while"):
            self.apply("start", "start-2", 0)
        self.assertEqual(self.session.snapshot(), before)

    def test_duplicate_event_id_is_rejected(self):
        self.apply("start", "reused", 0)
        before = self.session.snapshot()
        with self.assertRaisesRegex(SessionControlError, "duplicate"):
            self.apply("pause", "reused", 1)
        self.assertEqual(self.session.snapshot(), before)

    def test_failed_transition_does_not_consume_event_id(self):
        with self.assertRaises(SessionControlError):
            self.apply("resume", "reusable", 0)
        self.apply("start", "reusable", 0)
        self.assertEqual(self.session.state, RUNNING)

    def test_tampered_snapshot_is_rejected(self):
        self.apply("start", "start-1", 0)
        snapshot = self.session.snapshot()
        snapshot["last_tick"] = 8
        with self.assertRaisesRegex(SessionControlError, "snapshot state or tick"):
            SessionController.from_snapshot(snapshot)

    def test_rehashed_impossible_transition_is_rejected(self):
        self.apply("start", "start-1", 0)
        snapshot = self.session.snapshot()
        snapshot["events"][0]["to_state"] = PAUSED
        # A caller can recompute a checksum; it still cannot make an illegal
        # transition history valid.
        record = {key: value for key, value in snapshot.items() if key != "revision"}
        snapshot["revision"] = hashlib.sha256(
            json.dumps(record, sort_keys=True, ensure_ascii=True,
                       separators=(",", ":"), allow_nan=False).encode("ascii")
        ).hexdigest()
        with self.assertRaisesRegex(SessionControlError, "invalid transition"):
            SessionController.from_snapshot(snapshot)

    def test_non_string_schema_is_rejected_before_comparison(self):
        snapshot = self.session.snapshot()
        snapshot["schema"] = _HostileEquality()
        with self.assertRaisesRegex(SessionControlError, "schema or trust"):
            SessionController.from_snapshot(snapshot)

    def test_non_string_transition_state_is_rejected_before_comparison(self):
        self.apply("start", "start-1", 0)
        snapshot = self.session.snapshot()
        snapshot["events"][0]["from_state"] = _HostileEquality()
        with self.assertRaisesRegex(SessionControlError, "invalid transition"):
            SessionController.from_snapshot(snapshot)

    def test_snapshot_is_detached(self):
        snapshot = self.session.snapshot()
        snapshot["events"].append({"external": True})
        self.assertEqual(self.session.snapshot()["events"], [])

    def test_event_limit_is_enforced(self):
        # One start, then alternating pause/resume transitions to the cap.
        self.apply("start", "event-0", 0)
        for index in range(1, MAX_EVENTS):
            action = "pause" if self.session.state == RUNNING else "resume"
            self.apply(action, f"event-{index}", index)
        before = self.session.snapshot()
        with self.assertRaisesRegex(SessionControlError, "event limit"):
            self.apply("pause" if self.session.state == RUNNING else "resume",
                       "overflow", MAX_EVENTS)
        self.assertEqual(self.session.snapshot(), before)

    def test_invalid_identifiers_fail_closed(self):
        for value in ("", "../dante", "bad id", "x" * 129, "\ud800"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(SessionControlError):
                    SessionController(value)
        with self.assertRaises(SessionControlError):
            self.apply("start", "../event", 0)

    def test_malformed_event_sequence_rejected(self):
        self.apply("start", "start-1", 0)
        snapshot = self.session.snapshot()
        snapshot["events"][0]["seq"] = True
        # Keep the old checksum; malformed native types must be caught before
        # relying on a matching checksum.
        with self.assertRaisesRegex(SessionControlError, "sequence"):
            SessionController.from_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
