"""Hermetic offline tests for the isolated audio chunk-framer contract."""
import unittest

from mojum.audio_chunk_framer import (
    FRAME_BYTES,
    FRAME_DURATION_NS,
    MAX_CHUNK_BYTES,
    MAX_TIME_NS,
    PCM16ChunkFramer,
    PROVENANCE,
    SAMPLE_PERIOD_NS,
    SAMPLE_RATE_HZ,
    SAMPLES_PER_FRAME,
    SCHEMA,
)
from mojum.audio_energy import measure_pcm16_frame


def new_framer(start_ns=0):
    return PCM16ChunkFramer(start_ns=start_ns, provenance=PROVENANCE)


class PCM16ChunkFramerTests(unittest.TestCase):
    def test_fragmented_odd_byte_boundaries_and_sample_clock(self):
        raw = bytes(range(256)) * 5  # 1280 bytes = two frames
        f = new_framer(start_ns=123)
        outputs = []
        offset = 0
        for idx, length in enumerate((1, 638, 2, 639)):
            emitted = f.feed(raw[offset:offset + length],
                             chunk_index=idx, byte_offset=offset)
            outputs.extend(emitted)
            offset += length
        self.assertEqual(offset, len(raw))
        self.assertEqual(b"".join(x.pcm for x in outputs), raw)
        self.assertEqual([x.frame_index for x in outputs], [0, 1])
        self.assertEqual([x.sample_offset for x in outputs], [0, 320])
        self.assertEqual([x.observed_at_ns for x in outputs],
                         [123, 123 + FRAME_DURATION_NS])
        for frame in outputs:
            self.assertEqual(frame.schema, SCHEMA)
            self.assertEqual(frame.origin, PROVENANCE)
            self.assertEqual(frame.clock, "simulated")
            self.assertEqual(frame.duration_ns, FRAME_DURATION_NS)
            self.assertEqual(len(frame.pcm), FRAME_BYTES)
        summary = f.finish()
        self.assertEqual(summary["bytes_received"], 1280)
        self.assertEqual(summary["samples_emitted"], 640)
        self.assertEqual(summary["frames_emitted"], 2)
        self.assertEqual(summary["chunks_seen"], 4)
        self.assertTrue(f.finished)
        self.assertEqual(f.pending_bytes, 0)

    def test_unchunked_and_fragmented_results_match(self):
        payload = bytes(range(256)) * 10
        whole = new_framer()
        expected = whole.feed(payload, chunk_index=0, byte_offset=0)
        self.assertEqual(len(expected), 4)
        self.assertEqual(whole.pending_bytes, 0)
        segmented = new_framer()
        actual = []
        offset = 0
        for i, n in enumerate((3, 637, 111, 127, 699, 983)):
            actual.extend(segmented.feed(payload[offset:offset + n],
                                         chunk_index=i, byte_offset=offset))
            offset += n
        self.assertEqual(offset, len(payload))
        self.assertEqual(tuple(actual), expected)
        self.assertEqual(whole.finish()["samples_emitted"],
                         segmented.finish()["samples_emitted"])

    def test_output_can_feed_existing_energy_sensor(self):
        framer = new_framer(start_ns=1_000_000)
        frame, = framer.feed(bytes(FRAME_BYTES), chunk_index=0, byte_offset=0)
        result = measure_pcm16_frame(frame.pcm,
                                     observed_at_ns=frame.observed_at_ns)
        self.assertEqual(result["sample_rate_hz"], SAMPLE_RATE_HZ)
        self.assertEqual(result["samples"], SAMPLES_PER_FRAME)
        self.assertFalse(result["energy_gate_open"])
        self.assertTrue(framer.finish()["finished"])

    def test_invalid_constructor_arguments(self):
        for start in (True, -1, 2**63, 1.0, "0", None):
            with self.subTest(start=start), self.assertRaises(ValueError):
                PCM16ChunkFramer(start_ns=start, provenance=PROVENANCE)
        for value in ("synthetic", "PRIVATE", "", None, True, 3):
            with self.subTest(provenance=value), self.assertRaises(ValueError):
                PCM16ChunkFramer(start_ns=0, provenance=value)

    def test_rejects_ambiguous_bytes_and_empty_or_large_chunks_atomically(self):
        framer = new_framer()
        for bad in (b"", bytearray(2), memoryview(b"xx"),
                    None, "PCM", bytes(MAX_CHUNK_BYTES + 1)):
            with self.subTest(type=type(bad)), self.assertRaises(ValueError):
                framer.feed(bad, chunk_index=0, byte_offset=0)
            self.assertEqual((framer.chunks_seen, framer.bytes_seen,
                              framer.frames_emitted, framer.pending_bytes),
                             (0, 0, 0, 0))
        result = framer.feed(bytes(FRAME_BYTES), chunk_index=0, byte_offset=0)
        self.assertEqual(len(result), 1)

    def test_rejects_duplicate_gapped_reordered_or_noninteger_offsets(self):
        f = new_framer()
        f.feed(b"x", chunk_index=0, byte_offset=0)
        for index, offset in ((0, 1), (2, 1), (1, 0),
                              (True, 1), (1.0, 1), (1, True),
                              (1, -1), (1, 1.0), (1, None)):
            with self.subTest(index=index, offset=offset):
                with self.assertRaises(ValueError):
                    f.feed(b"y", chunk_index=index, byte_offset=offset)
            self.assertEqual((f.chunks_seen, f.bytes_seen, f.pending_bytes),
                             (1, 1, 1))
        f.feed(b"z", chunk_index=1, byte_offset=1)
        self.assertEqual(f.pending_bytes, 2)

    def test_finish_rejects_partial_and_preserves_recoverable_state(self):
        f = new_framer()
        self.assertEqual(f.feed(b"x", chunk_index=0, byte_offset=0), ())
        with self.assertRaisesRegex(ValueError, "incomplete"):
            f.finish()
        self.assertFalse(f.finished)
        self.assertEqual(f.pending_bytes, 1)
        result = f.feed(bytes(FRAME_BYTES - 1), chunk_index=1, byte_offset=1)
        self.assertEqual(len(result), 1)
        self.assertTrue(f.finish()["finished"])
        self.assertEqual(f.finish()["frames_emitted"], 1)  # idempotent
        with self.assertRaisesRegex(ValueError, "finished"):
            f.feed(bytes(FRAME_BYTES), chunk_index=2, byte_offset=FRAME_BYTES)

    def test_end_of_int64_clock_allows_exact_boundary(self):
        start = MAX_TIME_NS - FRAME_DURATION_NS
        f = new_framer(start_ns=start)
        one, = f.feed(bytes(FRAME_BYTES), chunk_index=0, byte_offset=0)
        self.assertEqual(one.observed_at_ns + one.duration_ns, MAX_TIME_NS)
        with self.assertRaisesRegex(ValueError, "overflows"):
            f.feed(bytes(FRAME_BYTES), chunk_index=1, byte_offset=FRAME_BYTES)
        self.assertEqual((f.chunks_seen, f.frames_emitted, f.bytes_seen),
                         (1, 1, FRAME_BYTES))
        self.assertTrue(f.finish()["finished"])

    def test_first_overflow_rejected_without_any_commit(self):
        f = new_framer(start_ns=MAX_TIME_NS)
        with self.assertRaises(ValueError):
            f.feed(bytes(FRAME_BYTES), chunk_index=0, byte_offset=0)
        self.assertEqual(f.chunks_seen, 0)
        self.assertEqual(f.frames_emitted, 0)
        self.assertEqual(f.pending_bytes, 0)

    def test_empty_stream_finishes_without_fabricated_audio(self):
        f = new_framer()
        summary = f.finish()
        self.assertEqual(summary["bytes_received"], 0)
        self.assertEqual(summary["samples_emitted"], 0)
        self.assertNotIn("pcm", summary)

    def test_pending_audio_is_bounded_by_single_frame(self):
        f = new_framer()
        for i in range(70):
            part = bytes((i,)) * (MAX_CHUNK_BYTES - 1)
            f.feed(part, chunk_index=i, byte_offset=f.bytes_seen)
            self.assertLess(f.pending_bytes, FRAME_BYTES)
            self.assertEqual(f.frames_emitted * FRAME_BYTES + f.pending_bytes,
                             f.bytes_seen)


if __name__ == "__main__":
    unittest.main()
