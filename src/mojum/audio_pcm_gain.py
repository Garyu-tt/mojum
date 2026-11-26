"""Bounded, deterministic offline gain for PUBLIC_SYNTHETIC mono PCM16LE.

A pure preprocessing primitive, not a microphone, ASR, VAD or playback bridge.
No file, network, clock, model or device access is performed.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

SCHEMA = "mojum.audio.pcm16-gain/1"
VISIBILITY = "PUBLIC_SYNTHETIC"
SUPPORTED_RATES_HZ = (8000, 16000, 32000, 48000)
MAX_DURATION_SECONDS = 10
MAX_FRAMES = 480_000
MAX_BYTES = MAX_FRAMES * 2
MAX_GAIN_DENOMINATOR = 1_000_000
MAX_GAIN = 4


class AudioGainError(ValueError):
    """Malformed, over-budget or non-public synthetic audio or gain settings."""


@dataclass(frozen=True)
class GainResult:
    schema: str
    sample_rate_hz: int
    frames: int
    gain_numerator: int
    gain_denominator: int
    input_peak_abs: int
    output_peak_abs: int
    clipped_samples: int
    changed_samples: int
    visibility: str
    pcm: bytes = field(repr=False)


def apply_pcm16_gain(
    pcm: bytes,
    *,
    sample_rate_hz: int,
    gain_numerator: int,
    gain_denominator: int = 1,
    visibility: str = VISIBILITY,
) -> GainResult:
    """Scale signed PCM16LE using exact rational gain, with saturation.

    Round nearest, exact half ties toward zero, then clamp to [-32768, 32767].
    Never use floating point or infer input provenance from a filename.
    ``clipped_samples`` counts pre-clamp out-of-range values, not samples
    already at a legal rail. Output frame count, rate and ordering are preserved.
    """
    if type(visibility) is not str or visibility != VISIBILITY:
        raise AudioGainError("requires PUBLIC_SYNTHETIC input")
    if type(pcm) is not bytes or not pcm or len(pcm) > MAX_BYTES or len(pcm) % 2:
        raise AudioGainError("invalid bounded mono PCM16LE bytes")
    if type(sample_rate_hz) is not int or sample_rate_hz not in SUPPORTED_RATES_HZ:
        raise AudioGainError("unsupported sample rate")
    frames = len(pcm) // 2
    if frames > MAX_FRAMES or frames > MAX_DURATION_SECONDS * sample_rate_hz:
        raise AudioGainError("PCM frame budget exceeded")
    if type(gain_denominator) is not int or not 1 <= gain_denominator <= MAX_GAIN_DENOMINATOR:
        raise AudioGainError("invalid rational gain denominator")
    if type(gain_numerator) is not int or not 0 <= gain_numerator <= MAX_GAIN * gain_denominator:
        raise AudioGainError("invalid rational gain numerator")

    output = bytearray(len(pcm))
    input_peak = output_peak = clipped = changed = 0
    for index, (sample,) in enumerate(struct.iter_unpack("<h", pcm)):
        input_peak = max(input_peak, abs(sample))
        quotient, remainder = divmod(abs(sample) * gain_numerator, gain_denominator)
        # The declared tie rule does not favor positive or negative samples.
        if 2 * remainder > gain_denominator:
            quotient += 1
        scaled = quotient if sample >= 0 else -quotient
        if scaled < -32768:
            scaled = -32768
            clipped += 1
        elif scaled > 32767:
            scaled = 32767
            clipped += 1
        changed += scaled != sample
        output_peak = max(output_peak, abs(scaled))
        struct.pack_into("<h", output, index * 2, scaled)

    return GainResult(
        schema=SCHEMA,
        sample_rate_hz=sample_rate_hz,
        frames=frames,
        gain_numerator=gain_numerator,
        gain_denominator=gain_denominator,
        input_peak_abs=input_peak,
        output_peak_abs=output_peak,
        clipped_samples=clipped,
        changed_samples=changed,
        visibility=VISIBILITY,
        pcm=bytes(output),
    )
