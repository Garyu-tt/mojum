"""Bounded offline PCM16 DC-offset diagnostics for PUBLIC_SYNTHETIC fixtures.

Not a microphone driver, voice detector, ASR system, authentication mechanism,
or audio correction stage. Raw audio is never stored in the returned report.
"""
from __future__ import annotations

import hashlib
import math
import struct

SCHEMA = "mojum.audio.dc-offset-diagnostic/1"
CLASSIFICATION = "PUBLIC_SYNTHETIC"
SAMPLE_RATE_HZ = 16_000
MAX_SAMPLES = 160_000  # Ten seconds at 16 kHz, 320 KiB of PCM.
_MAX_BYTES = MAX_SAMPLES * 2
_NORMALIZER = 32768.0


def inspect_pcm16_dc_offset(
    pcm: bytes, *, classification: str,
    sample_rate_hz: int = SAMPLE_RATE_HZ,
) -> dict:
    """Return reproducible scalar DC/AC RMS and clipping metrics.

    Accepts only an explicitly classified public synthetic mono PCM16LE byte
    string. Integer sums and squared sums are exact; normalized RMS values
    are floating-point diagnostics, not calibrated measurements.
    """
    if type(classification) is not str or classification != CLASSIFICATION:
        raise ValueError("requires explicit PUBLIC_SYNTHETIC classification")
    if type(sample_rate_hz) is not int or sample_rate_hz != SAMPLE_RATE_HZ:
        raise ValueError("requires integer 16000 Hz mono PCM16LE")
    if type(pcm) is not bytes:
        raise ValueError("PCM must be exact immutable bytes")
    byte_count = len(pcm)
    if byte_count == 0 or byte_count > _MAX_BYTES or byte_count % 2:
        raise ValueError("PCM must contain 1..160000 whole PCM16 samples")

    count = byte_count // 2
    total = 0
    sum_squares = 0
    clipped = 0
    zeros = 0
    minimum = 32767
    maximum = -32768
    # iter_unpack avoids allocating a second full-sized audio sample array.
    for (sample,) in struct.iter_unpack("<h", pcm):
        total += sample
        sum_squares += sample * sample
        clipped += (sample == -32768 or sample == 32767)
        zeros += (sample == 0)
        minimum = min(minimum, sample)
        maximum = max(maximum, sample)

    # n * sum(x^2) - sum(x)^2 is exact, nonnegative by Cauchy-Schwarz.
    ac_variance_numerator = count * sum_squares - total * total
    assert ac_variance_numerator >= 0
    # DC fraction is abs(mean) / RMS, conventionally zero for silence.
    dc_fraction = (abs(total) / math.sqrt(count * sum_squares)
                   if sum_squares else 0.0)
    return {
        "schema": SCHEMA,
        "classification": CLASSIFICATION,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "channel_count": 1,
        "sample_format": "signed-16-bit-little-endian",
        "samples": count,
        "duration_ns": count * 62_500,
        "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
        "sum_samples": total,
        "sum_squares": sum_squares,
        "sample_min": minimum,
        "sample_max": maximum,
        "zero_samples": zeros,
        "clipped_samples": clipped,
        "dc_mean_normalized": total / (count * _NORMALIZER),
        "rms_normalized": math.sqrt(sum_squares / count) / _NORMALIZER,
        "ac_rms_normalized": math.sqrt(ac_variance_numerator) / (count * _NORMALIZER),
        "dc_fraction_of_rms": min(1.0, dc_fraction),
        "diagnostic_only": True,
        "raw_audio_retained": False,
    }
