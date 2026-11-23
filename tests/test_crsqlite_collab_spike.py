"""CPU-only adversarial checks for #759; no native extension is loaded."""
import json
import random
import unittest

from mojum.crsqlite_collab_spike import (
    MAX_EVENTS, MAX_JSON_BYTES, Replica, WIRE, decode_events, event,
    partition_fixture, validate_event,
)


class CrSqliteCollabSpikeTests(unittest.TestCase):
    def test_partition_converges(self):
        result = partition_fixture()
        self.assertTrue(result["converged"])
        self.assertEqual(result["event_count"], 6)
        self.assertEqual(result["view"], {
            "view.v1.collapsed": True,
            "view.v1.x": -5,
            "view.v2.x": 25,
        })

    def test_replay_order_and_duplicate_invariant(self):
        changes = [
            event("r1", 1, "view.v1.x", 7),
            event("r2", 1, "view.v1.x", 9),
            event("r3", 1, "view.v1.collapsed", True),
            event("r3", 2, "view.v1.collapsed", None),
        ]
        expected = Replica()
        expected.merge(changes)
        for seed in range(30):
            batch = changes + changes
            random.Random(seed).shuffle(batch)
            check = Replica()
            check.merge(batch)
            self.assertEqual(check.digest(), expected.digest())
            self.assertEqual(check.view(), {"view.v1.x": 9})

    def test_merge_is_commutative_and_idempotent(self):
        a = [event("r1", 1, "view.v1.x", 50)]
        b = [event("r2", 1, "view.v1.x", 51)]
        left, right = Replica(), Replica()
        left.merge(a)
        left.merge(b)
        left.merge(b)
        right.merge(b)
        right.merge(a)
        self.assertEqual(left.frame(), right.frame())

    def test_higher_logical_clock_wins(self):
        state = Replica()
        state.merge([event("r9", 1, "view.v1.x", 9),
                     event("r1", 2, "view.v1.x", 12)])
        self.assertEqual(state.view()["view.v1.x"], 12)

    def test_tombstone_does_not_resurrect(self):
        state = Replica()
        state.merge([event("r1", 1, "view.v1.x", 1),
                     event("r1", 2, "view.v1.x", None)])
        self.assertEqual(state.view(), {})
        state.merge([event("r1", 1, "view.v1.x", 1)])
        self.assertEqual(state.view(), {})

    def test_export_roundtrip_and_digest(self):
        state = Replica()
        state.merge([event("r1", 1, "view.v1.x", -4)])
        replica = Replica()
        replica.merge_frame(state.frame())
        self.assertEqual(replica.digest(), state.digest())
        self.assertEqual(replica.view(), state.view())

    def test_conflicting_retransmission_is_atomic(self):
        state = Replica()
        original = event("r1", 1, "view.v1.x", 1)
        state.merge([original])
        before = state.frame()
        with self.assertRaises(ValueError):
            state.merge([event("r2", 1, "view.v2.x", 10),
                         event("r1", 1, "view.v1.x", 2)])
        self.assertEqual(state.frame(), before)

    def test_private_and_authority_fields_denied(self):
        forbidden = (
            "PRIVATE", "PUBLIC", "INTERNAL", "", "PUBLIC_SYNTHETIC ",
        )
        for visibility in forbidden:
            op = event("r1", 1, "view.v1.x", 2)
            op["visibility"] = visibility
            with self.subTest(visibility=visibility), self.assertRaises(PermissionError):
                validate_event(op)
        for key in (
            "noosphere.memory", "token", "credential", "hostname",
            "motor.intent", "graph.node", "view.v1.secret",
            "../view.v1.x", "view.v1.x;DROP TABLE",
        ):
            with self.subTest(key=key), self.assertRaises(PermissionError):
                event("r1", 1, key, 1)

    def test_invalid_identity_and_counter(self):
        for actor in ("", "host001", "r0", "r1.2", "r1/secret", "r999999", 1):
            with self.subTest(actor=actor), self.assertRaises(ValueError):
                event(actor, 1, "view.v1.x", 2)
        for seq in (-1, 0, 1.0, True, 1_000_001):
            with self.subTest(seq=seq), self.assertRaises(ValueError):
                event("r1", seq, "view.v1.x", 2)

    def test_invalid_values_strict_type_and_budget(self):
        for value in (True, 1.5, "22", {}, [], 2049, -2049, float("nan")):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                event("r1", 1, "view.v1.x", value)
        for value in (0, "false", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                event("r1", 1, "view.v1.collapsed", value)

    def test_wrong_version_or_extra_field_denied(self):
        original = event("r1", 1, "view.v1.x", 1)
        for k, v in (("wire", WIRE + "/2"), ("extra", "injected")):
            tampered = dict(original, **{k: v})
            with self.subTest(k=k), self.assertRaises((PermissionError, ValueError)):
                validate_event(tampered)

    def test_duplicate_json_fields_denied(self):
        op = event("r1", 1, "view.v1.x", 1)
        raw = json.dumps(op).replace('"counter": 1', '"counter": 1, "counter": 1')
        with self.assertRaises(ValueError):
            decode_events(("[" + raw + "]").encode())

    def test_untrusted_frame_rejects_malformed_and_depth(self):
        for raw in (b'null', b'{}', b'"ok"', b'[false]', b'[{]', b'',
                    b'[' * 5000 + b']' * 5000,
                    b'x' * (MAX_JSON_BYTES + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                decode_events(raw)

    def test_finite_max_events_and_atomicity(self):
        state = Replica()
        batch = [event("r1", i + 1, "view.v1.x", i % 10)
                 for i in range(MAX_EVENTS)]
        state.merge(batch)
        self.assertEqual(len(state.events()), MAX_EVENTS)
        with self.assertRaises(ValueError):
            state.merge([event("r2", 1, "view.v1.x", 1)])
        self.assertEqual(len(state.events()), MAX_EVENTS)
        with self.assertRaises(ValueError):
            state.merge(batch + batch[:1])

    def test_frames_are_not_network_or_organism_commands(self):
        import mojum.crsqlite_collab_spike as m
        for name in ("send", "connect", "enroll", "write_journal",
                     "execute_motor", "load_extension"):
            self.assertFalse(hasattr(m, name))


if __name__ == "__main__":
    unittest.main()
