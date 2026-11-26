from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from mojum.fingersense_ingress import (
    FRAME_SCHEMA,
    PUBLIC_SYNTHETIC,
    FingerSenseIngressError,
    IngressState,
    admit_frame,
)


def frame(**changes):
    value = {
        "schema": FRAME_SCHEMA,
        "visibility": PUBLIC_SYNTHETIC,
        "consent": "explicit",
        "source_id": "fixture-hand-left",
        "source_revision": 2,
        "sequence": 0,
        "tick": 10,
        "samples": [
            {"finger_id": "index", "channel_id": "pressure", "value_milli": 125000},
            {"finger_id": "thumb", "channel_id": "contact", "value_milli": 1000000},
        ],
    }
    value.update(changes)
    return value


class FingerSenseIngressTests(unittest.TestCase):
    def test_admits_detached_sorted_immutable_frame(self):
        raw = frame()
        admitted, state = admit_frame(raw)
        raw["samples"][0]["value_milli"] = 999
        self.assertEqual(admitted.samples[0].finger_id, "index")
        self.assertEqual(admitted.samples[0].value_milli, 125000)
        self.assertEqual([s.finger_id for s in admitted.samples], ["index", "thumb"])
        with self.assertRaises(FrozenInstanceError):
            admitted.tick = 99
        self.assertEqual(state.last_sequence, 0)
        self.assertEqual(len(admitted.sha256), 64)

    def test_contiguous_sequence_and_increasing_tick(self):
        _, state = admit_frame(frame())
        next_frame, next_state = admit_frame(frame(sequence=1, tick=11), state)
        self.assertEqual(next_frame.sequence, 1)
        self.assertEqual(next_state.last_tick, 11)

    def test_rejects_sequence_gap_duplicate_and_tick_replay(self):
        _, state = admit_frame(frame())
        for candidate in (
            frame(sequence=2, tick=11),
            frame(sequence=1, tick=10),
            frame(sequence=1, tick=9),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(FingerSenseIngressError):
                    admit_frame(candidate, state)

    def test_rejects_source_epoch_change_without_new_stream(self):
        _, state = admit_frame(frame())
        with self.assertRaises(FingerSenseIngressError):
            admit_frame(frame(sequence=1, tick=11, source_revision=3), state)
        with self.assertRaises(FingerSenseIngressError):
            admit_frame(frame(sequence=1, tick=11, source_id="other"), state)

    def test_rejects_non_exact_envelopes_and_unsafe_provenance(self):
        for candidate in (
            frame(visibility="PRIVATE"),
            frame(consent="implicit"),
            frame(extra="ignored"),
            frame(schema="future"),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(FingerSenseIngressError):
                    admit_frame(candidate)

    def test_rejects_boolean_float_and_out_of_range_values(self):
        for bad in (True, 1.0, -1, 1_000_001):
            candidate = frame()
            candidate["samples"][0]["value_milli"] = bad
            with self.subTest(value=bad):
                with self.assertRaises(FingerSenseIngressError):
                    admit_frame(candidate)

    def test_rejects_duplicate_channels_and_duplicate_keys(self):
        candidate = frame(samples=[
            {"finger_id": "index", "channel_id": "pressure", "value_milli": 1},
            {"finger_id": "index", "channel_id": "pressure", "value_milli": 2},
        ])
        with self.assertRaises(FingerSenseIngressError):
            admit_frame(candidate)
        candidate = frame()
        candidate["samples"][0][1] = "pressure"
        with self.assertRaises(FingerSenseIngressError):
            admit_frame(candidate)

    def test_rejects_oversized_or_empty_sample_inventory(self):
        with self.assertRaises(FingerSenseIngressError):
            admit_frame(frame(samples=[]))
        too_many = [
            {"finger_id": f"f{i}", "channel_id": "pressure", "value_milli": 1}
            for i in range(65)
        ]
        with self.assertRaises(FingerSenseIngressError):
            admit_frame(frame(samples=too_many))

    def test_canonical_digest_ignores_sample_order(self):
        first = frame()
        second = frame(samples=list(reversed(first["samples"])))
        admitted_a, _ = admit_frame(first)
        admitted_b, _ = admit_frame(second)
        self.assertEqual(admitted_a.sha256, admitted_b.sha256)

    def test_rejects_invalid_state_type_and_contents(self):
        with self.assertRaises(FingerSenseIngressError):
            admit_frame(frame(), previous=object())
        forged = IngressState("fixture-hand-left", 2, True, 10, "not-a-digest")
        with self.assertRaises(FingerSenseIngressError):
            admit_frame(frame(sequence=1, tick=11), previous=forged)


if __name__ == "__main__":
    unittest.main()
