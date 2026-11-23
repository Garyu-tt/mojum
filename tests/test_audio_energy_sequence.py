import hashlib
import json
import struct
import unittest
from unittest.mock import patch

from mojum.audio_energy import (
    FRAME_DURATION_NS,
    PCM_BYTES,
    measure_pcm16_frame,
)
from mojum.audio_energy_sequence import (
    MAX_FRAMES,
    MAX_PCM_BYTES,
    MAX_TIME_NS,
    SCHEMA,
    measure_pcm16_sequence,
)


SILENCE = bytes(PCM_BYTES)
LOUD = struct.pack("<320h", *([16384, -16384] * 160))


class EnergySequenceTests(unittest.TestCase):
    def test_replay_digest_and_exact_single_frame_measurements(self):
        a = measure_pcm16_sequence([SILENCE, LOUD], start_ns=40_000_000)
        b = measure_pcm16_sequence((SILENCE, LOUD), start_ns=40_000_000)
        self.assertEqual(a, b)
        self.assertEqual(a["schema"], SCHEMA)
        self.assertEqual(a["frame_count"], 2)
        self.assertEqual(a["pcm_bytes_processed"], 1280)
        self.assertEqual(a["frame_duration_ns"], FRAME_DURATION_NS)
        self.assertEqual(a["last_observed_at_ns"], 60_000_000)
        self.assertEqual(a["open_gate_frames"], 1)
        self.assertEqual(a["max_rms_normalized"], 0.5)
        self.assertFalse(a["raw_audio_retained"])
        self.assertEqual(a["classification"], "energy-only-not-speech")
        self.assertEqual(a["visibility"], "PRIVATE")
        for i, pcm in enumerate((SILENCE, LOUD)):
            expected = measure_pcm16_frame(
                pcm, observed_at_ns=40_000_000 + i * FRAME_DURATION_NS
            )
            self.assertEqual(
                a["frames"][i],
                {field: expected[field] for field in (
                    "observed_at_ns", "rms_normalized",
                    "peak_normalized", "energy_gate_open",
                )},
            )

    def test_checksum_is_canonical_scalar_json_not_pcm_attestation(self):
        result = measure_pcm16_sequence([LOUD, SILENCE], start_ns=0)
        digest = result.pop("sha256")
        canonical = (json.dumps(
            result, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ) + "\n").encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), digest)
        self.assertEqual(len(digest), 64)
        self.assertNotIn("samples", result["frames"][0])
        self.assertNotIn("transcript", result)
        self.assertNotIn("pcm", result["frames"][0])
        self.assertFalse(any(isinstance(v, bytes) for v in result.values()))
        self.assertFalse(any(isinstance(v, bytes) for e in result["frames"] for v in e.values()))

    def test_strong_energy_threshold_is_inclusive(self):
        same = measure_pcm16_sequence([LOUD], start_ns=0, threshold=0.5)
        higher = measure_pcm16_sequence([LOUD], start_ns=0, threshold=0.500001)
        self.assertEqual(same["open_gate_frames"], 1)
        self.assertEqual(higher["open_gate_frames"], 0)
        self.assertNotEqual(same["sha256"], higher["sha256"])
        self.assertEqual(same["frames"][0]["peak_normalized"], 0.5)

    def test_maximum_bounded_sequence(self):
        output = measure_pcm16_sequence([SILENCE] * MAX_FRAMES, start_ns=0)
        self.assertEqual(output["frame_count"], MAX_FRAMES)
        self.assertEqual(output["pcm_bytes_processed"], MAX_PCM_BYTES)
        self.assertEqual(output["last_observed_at_ns"], (MAX_FRAMES - 1) * FRAME_DURATION_NS)
        self.assertEqual(output["open_gate_frames"], 0)
        self.assertEqual(len(output["frames"]), 256)
        with self.assertRaises(ValueError):
            measure_pcm16_sequence([SILENCE] * (MAX_FRAMES + 1), start_ns=0)

    def test_time_boundary_and_guard_before_processing(self):
        last_start = MAX_TIME_NS - FRAME_DURATION_NS
        end = measure_pcm16_sequence([SILENCE, SILENCE], start_ns=last_start)
        self.assertEqual(end["last_observed_at_ns"], MAX_TIME_NS)
        with patch("mojum.audio_energy_sequence.measure_pcm16_frame") as meter:
            with self.assertRaisesRegex(ValueError, "overflow"):
                measure_pcm16_sequence(
                    [SILENCE, SILENCE], start_ns=last_start + 1
                )
            meter.assert_not_called()

    def test_rejects_timestamp_forgery_and_bools(self):
        for start in (True, False, -1, MAX_TIME_NS + 1, 0.0, "0", None):
            with self.subTest(start=start):
                with self.assertRaises(ValueError):
                    measure_pcm16_sequence([SILENCE], start_ns=start)

    def test_rejects_empty_or_nonconcrete_containers(self):
        for frames in ([], (), SILENCE, {0: SILENCE}, iter((SILENCE,)), None):
            with self.subTest(container_type=type(frames)):
                with self.assertRaises(ValueError):
                    measure_pcm16_sequence(frames, start_ns=0)

    def test_invalid_late_frame_fails_before_any_measurement(self):
        for invalid in (bytearray(PCM_BYTES), memoryview(SILENCE),
                        SILENCE[:-1], SILENCE + b"\x00", None):
            with self.subTest(invalid_type=type(invalid)):
                with patch("mojum.audio_energy_sequence.measure_pcm16_frame") as meter:
                    with self.assertRaises(ValueError):
                        measure_pcm16_sequence([LOUD, invalid], start_ns=0)
                    meter.assert_not_called()

    def test_origin_clock_consent_not_inferred(self):
        real = measure_pcm16_sequence(
            [SILENCE], start_ns=12, clock="monotonic",
            origin="microphone-opt-in",
        )
        self.assertEqual(real["origin"], "microphone-opt-in")
        self.assertEqual(real["clock"], "monotonic")
        self.assertEqual(real["visibility"], "PRIVATE")
        for clock, origin in (
            ("simulated", "microphone-opt-in"),
            ("wall", "synthetic"),
            ("monotonic", "unknown"),
            (1, "synthetic"),
        ):
            with self.subTest(clock=clock, origin=origin):
                with self.assertRaises(ValueError):
                    measure_pcm16_sequence([SILENCE], start_ns=0, clock=clock, origin=origin)

    def test_bad_thresholds_fail_closed(self):
        for threshold in (
            True, -1, 0, 1.001, float("nan"), float("inf"),
            -float("inf"), 10**1000, "0.5",
        ):
            with self.subTest(threshold=str(threshold)[:30]):
                with self.assertRaises(ValueError):
                    measure_pcm16_sequence([SILENCE], start_ns=0, threshold=threshold)

    def test_mutating_original_input_container_does_not_change_result(self):
        frames = [SILENCE, LOUD]
        report = measure_pcm16_sequence(frames, start_ns=0)
        frames.clear()
        self.assertEqual(report, measure_pcm16_sequence((SILENCE, LOUD), start_ns=0))
        self.assertEqual(len(report["frames"]), 2)


if __name__ == "__main__":
    unittest.main()
