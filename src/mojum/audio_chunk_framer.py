"""Bounded, deterministic offline PCM16 chunk-to-frame adapter.

Only caller-supplied PUBLIC_SYNTHETIC mono 16-kHz PCM16LE bytes are admitted.
No microphone capture, external I/O, models, resampling or storage.
"""
from __future__ import annotations

from dataclasses import dataclass

SCHEMA = "mojum.audio.chunk-frame/1"
SUMMARY_SCHEMA = "mojum.audio.chunk-stream-summary/1"
PROVENANCE = "PUBLIC_SYNTHETIC"
SAMPLE_RATE_HZ = 16_000
SAMPLE_PERIOD_NS = 62_500
SAMPLES_PER_FRAME = 320
FRAME_DURATION_NS = SAMPLES_PER_FRAME * SAMPLE_PERIOD_NS
FRAME_BYTES = SAMPLES_PER_FRAME * 2
MAX_CHUNK_BYTES = FRAME_BYTES * 16
MAX_FRAMES = 100_000
MAX_CHUNKS = 100_000
MAX_STREAM_BYTES = FRAME_BYTES * MAX_FRAMES
MAX_TIME_NS = 2**63 - 1


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """One detached 20 ms frame; pcm remains with the caller, not the framer."""

    schema: str
    frame_index: int
    sample_offset: int
    observed_at_ns: int
    duration_ns: int
    pcm: bytes
    origin: str = PROVENANCE
    clock: str = "simulated"


class PCM16ChunkFramer:
    """Assemble arbitrary byte partitions into exact 640-byte PCM frames.

    A chunk is addressed by its zero-based sequence index and absolute *byte*
    offset, not sample offset. Byte offsets permit odd-byte fragmentation.
    feed() is atomic on validation failures: no counters or pending bytes move.
    It is a single-owner object; concurrent callers must synchronize externally.
    """

    def __setattr__(self, name: str, value: object) -> None:
        # The admitted clock origin is immutable through the public Python API.
        if name == "_start_ns" and hasattr(self, "_start_ns"):
            raise AttributeError("start clock origin is immutable")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if name == "_start_ns":
            raise AttributeError("start clock origin is immutable")
        object.__delattr__(self, name)

    def __init__(self, *, start_ns: int = 0, provenance: str):
        if type(start_ns) is not int or not 0 <= start_ns <= MAX_TIME_NS:
            raise ValueError("start_ns must be a bounded native integer")
        if type(provenance) is not str or provenance != PROVENANCE:
            raise ValueError("explicit PUBLIC_SYNTHETIC provenance required")
        object.__setattr__(self, "_start_ns", start_ns)
        self._pending = b""
        self._chunks_seen = 0
        self._bytes_seen = 0
        self._frames_emitted = 0
        self._finished = False

    @property
    def start_ns(self) -> int:
        """Fixed, read-only simulated origin admitted at construction."""
        return self._start_ns

    @property
    def chunks_seen(self) -> int:
        return self._chunks_seen

    @property
    def bytes_seen(self) -> int:
        return self._bytes_seen

    @property
    def frames_emitted(self) -> int:
        return self._frames_emitted

    @property
    def pending_bytes(self) -> int:
        return len(self._pending)

    @property
    def finished(self) -> bool:
        return self._finished

    def feed(self, pcm_chunk: bytes, *, chunk_index: int, byte_offset: int) -> tuple[AudioFrame, ...]:
        """Return zero or more detached frames, never a partial frame."""
        if self._finished:
            raise ValueError("stream already finished")
        if type(chunk_index) is not int or chunk_index != self._chunks_seen:
            raise ValueError("chunk_index must be contiguous and start at zero")
        if type(byte_offset) is not int or byte_offset != self._bytes_seen:
            raise ValueError("byte_offset must be contiguous and start at zero")
        if type(pcm_chunk) is not bytes or not 1 <= len(pcm_chunk) <= MAX_CHUNK_BYTES:
            raise ValueError("require 1..10240 native PCM16LE bytes per chunk")
        if self._chunks_seen >= MAX_CHUNKS:
            raise ValueError("chunk count limit reached")
        new_bytes_seen = self._bytes_seen + len(pcm_chunk)
        if new_bytes_seen > MAX_STREAM_BYTES:
            raise ValueError("stream byte budget exceeded")

        combined = self._pending + pcm_chunk
        count = len(combined) // FRAME_BYTES
        if self._frames_emitted + count > MAX_FRAMES:
            raise ValueError("frame count limit reached")
        frames = []
        for index_in_chunk in range(count):
            frame_index = self._frames_emitted + index_in_chunk
            sample_offset = frame_index * SAMPLES_PER_FRAME
            timestamp = self._start_ns + sample_offset * SAMPLE_PERIOD_NS
            if timestamp > MAX_TIME_NS - FRAME_DURATION_NS:
                raise ValueError("frame timestamp/duration overflows 64-bit clock")
            start = index_in_chunk * FRAME_BYTES
            frames.append(
                AudioFrame(
                    schema=SCHEMA,
                    frame_index=frame_index,
                    sample_offset=sample_offset,
                    observed_at_ns=timestamp,
                    duration_ns=FRAME_DURATION_NS,
                    pcm=combined[start:start + FRAME_BYTES],
                )
            )

        # Commit only after the complete input and all outputs are validated.
        self._pending = combined[count * FRAME_BYTES:]
        self._bytes_seen = new_bytes_seen
        self._chunks_seen += 1
        self._frames_emitted += count
        return tuple(frames)

    def finish(self) -> dict[str, int | str | bool]:
        """Finalize only an exact number of frames. Never silently pad/drop."""
        if self._pending:
            raise ValueError("incomplete trailing PCM16 frame")
        self._finished = True
        return {
            "schema": SUMMARY_SCHEMA,
            "origin": PROVENANCE,
            "clock": "simulated",
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "frames_emitted": self._frames_emitted,
            "chunks_seen": self._chunks_seen,
            "bytes_received": self._bytes_seen,
            "samples_emitted": self._frames_emitted * SAMPLES_PER_FRAME,
            "finished": True,
        }
