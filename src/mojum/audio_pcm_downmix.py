"""Bounded, deterministic stereo PCM16LE to mono preprocessing for offline synthetic audio.

This does not change the sample rate or provide consent, capture, ASR, or VAD.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

SCHEMA = "mojum.audio.pcm16-stereo-downmix/1"
MAX_INPUT_BYTES = 640_000
MAX_FRAMES = MAX_INPUT_BYTES // 4
MAX_DURATION_SECONDS = 10
MIN_SAMPLE_RATE_HZ = 8_000
MAX_SAMPLE_RATE_HZ = 192_000


@dataclass(frozen=True, slots=True)
class MonoPCM16:
    schema: str
    pcm16le: bytes
    sample_rate_hz: int
    frames: int
    input_channels: int
    output_channels: int
    visibility: str
    rounding: str


def downmix_stereo_pcm16le(
    pcm: bytes, *, sample_rate_hz: int, visibility: str = "PUBLIC_SYNTHETIC"
) -> MonoPCM16:
    """Downmix interleaved signed L/R frames using integer mean, ties toward zero.

    Exactly four input bytes per frame, two output bytes per frame. Rejects
    unbounded, private, or ambiguous inputs before allocating output storage.
    The caller supplies the sample rate; unframed PCM cannot attest to it.
    """
    if type(visibility) is not str or visibility != "PUBLIC_SYNTHETIC":
        raise ValueError("only explicit PUBLIC_SYNTHETIC audio is permitted")
    if type(sample_rate_hz) is not int or not (
        MIN_SAMPLE_RATE_HZ <= sample_rate_hz <= MAX_SAMPLE_RATE_HZ
    ):
        raise ValueError("sample_rate_hz must be an integer from 8000 to 192000")
    if type(pcm) is not bytes:
        raise ValueError("pcm must be immutable bytes")
    size = len(pcm)
    if not 4 <= size <= MAX_INPUT_BYTES or size % 4:
        raise ValueError("pcm must contain 1..160000 complete stereo PCM16LE frames")
    frames = size // 4
    if frames > sample_rate_hz * MAX_DURATION_SECONDS:
        raise ValueError("pcm duration exceeds ten seconds at the declared rate")

    output = bytearray(frames * 2)
    for index, (left, right) in enumerate(struct.iter_unpack("<hh", pcm)):
        total = left + right
        mono = total // 2 if total >= 0 else -((-total) // 2)
        struct.pack_into("<h", output, index * 2, mono)
    return MonoPCM16(
        schema=SCHEMA,
        pcm16le=bytes(output),
        sample_rate_hz=sample_rate_hz,
        frames=frames,
        input_channels=2,
        output_channels=1,
        visibility=visibility,
        rounding="integer-mean-toward-zero",
    )
