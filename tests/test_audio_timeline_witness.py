"""CPU/stdlib, credential-free tests for synthetic audio timeline admission."""

import copy
import unittest

from mojum.audio_timeline_witness import (
    AudioTimelineError, FRAME_SCHEMA, MAX_NS, SCOPE, witness,
)

START = 1_700_000_000_000_000_000


def sample_frame(seq=0, offset=0, ns=START, tick=7, *, samples=160):
    return {
        "schema": FRAME_SCHEMA, "scope": SCOPE, "session_id": "fixture.01",
        "sequence": seq, "tick": tick, "sample_offset": offset,
        "captured_at_ns": ns, "sample_rate_hz": 16000, "channels": 1,
        "network_access": False, "physical_actuation": False,
        "pcm16le": bytes([0x01, 0xFE]) * samples,
    }


class AudioTimelineWitnessTests(unittest.TestCase):
    def setUp(self):
        self.frames = [
            sample_frame(),
            sample_frame(1, 160, START + 10_000_000, 7, samples=320),
            sample_frame(2, 480, START + 30_000_000, 8, samples=480),
        ]

    def assert_refused(self, frames):
        with self.assertRaises(AudioTimelineError):
            witness(frames)

    def test_contiguous_multiple_sizes_exact_time(self):
        receipt = witness(self.frames)
        self.assertEqual((receipt["frames"], receipt["samples"]), (3, 960))
        self.assertEqual(receipt["duration_ns"], 60_000_000)
        self.assertEqual(receipt["end_ns_exclusive"], START + 60_000_000)
        self.assertEqual((receipt["first_tick"], receipt["last_tick"]), (7, 8))
        self.assertEqual(len(receipt["chain_sha256"]), 64)
        self.assertNotIn("pcm16le", str(receipt))

    def test_deterministic_and_inputs_not_mutated(self):
        original = copy.deepcopy(self.frames)
        a = witness(self.frames)
        self.assertEqual(a, witness(copy.deepcopy(self.frames)))
        self.assertEqual(original, self.frames)
        self.frames[2]["pcm16le"] = b"\x00\x00" * 480
        self.assertNotEqual(a["chain_sha256"], witness(self.frames)["chain_sha256"])

    def test_empty_overbudget_and_non_plain_collection(self):
        self.assert_refused([])
        self.assert_refused([sample_frame()] * 257)
        self.assert_refused(iter(self.frames))
        self.assert_refused(tuple())

    def test_missing_extra_and_hostile_keys(self):
        f = sample_frame()
        del f["scope"]
        self.assert_refused([f])
        f = sample_frame()
        f["surprise"] = "ignored?"
        self.assert_refused([f])

        class StringTrick(str):
            def __eq__(self, other):
                raise AssertionError("must not invoke hostile equality")
            __hash__ = str.__hash__

        f = sample_frame()
        f[StringTrick("schema")] = f.pop("schema")
        self.assert_refused([f])

    def test_scope_and_capability_gates(self):
        for key, value in [
            ("scope", "PRIVATE"), ("network_access", True),
            ("physical_actuation", True), ("schema", "unknown"),
            ("scope", StringImpostor(SCOPE)),
        ]:
            f = sample_frame()
            f[key] = value
            with self.subTest(key=key, value=value):
                self.assert_refused([f])

    def test_audio_contract_gates(self):
        for key, value in [
            ("sample_rate_hz", 48000), ("sample_rate_hz", True),
            ("channels", 2), ("channels", True),
            ("pcm16le", bytearray(320)), ("pcm16le", b"\0" * 319),
            ("pcm16le", b"\0" * 961), ("pcm16le", b""),
        ]:
            f = sample_frame()
            f[key] = value
            with self.subTest(key=key, value=type(value)):
                self.assert_refused([f])

    def test_sequence_gap_duplicate_and_bool(self):
        for value in [0, 2, True, -1, MAX_NS + 1]:
            f = copy.deepcopy(self.frames)
            f[1]["sequence"] = value
            with self.subTest(value=value):
                self.assert_refused(f)

    def test_sample_offset_gap_overlap_and_bool(self):
        for value in [159, 161, True, -1]:
            f = copy.deepcopy(self.frames)
            f[1]["sample_offset"] = value
            with self.subTest(value=value):
                self.assert_refused(f)

    def test_timestamps_detect_jitter_and_bool(self):
        for value in [START + 10_000_001, START + 9_999_999, True, -1]:
            f = copy.deepcopy(self.frames)
            f[1]["captured_at_ns"] = value
            with self.subTest(value=value):
                self.assert_refused(f)

    def test_clock_overflow_at_last_frame(self):
        self.assert_refused([sample_frame(ns=MAX_NS - 9_999_999)])

    def test_logical_ticks_do_not_move_backwards(self):
        f = copy.deepcopy(self.frames)
        f[2]["tick"] = 6
        self.assert_refused(f)
        f[2]["tick"] = True
        self.assert_refused(f)

    def test_session_switch_invalid_id_and_forged_schema(self):
        f = copy.deepcopy(self.frames)
        f[2]["session_id"] = "different"
        self.assert_refused(f)
        for bad in ["", "a" * 65, "../oops", "\ud800", 123, StringImpostor("fixture.01")]:
            with self.subTest(bad=type(bad)):
                x = sample_frame()
                x["session_id"] = bad
                self.assert_refused([x])

    def test_exact_size_frame_cap_and_non_contiguity(self):
        frames = []
        for i in range(256):
            frames.append(sample_frame(i, i * 160, START + i * 10_000_000))
        self.assertEqual(witness(frames)["samples"], 40960)
        self.assert_refused(frames + [sample_frame()])
        self.assert_refused([frames[1], frames[0]])


class StringImpostor(str):
    pass


if __name__ == "__main__":
    unittest.main()
