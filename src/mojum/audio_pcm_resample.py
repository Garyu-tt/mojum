"""Bounded opt-in, CPU-only PCM16LE mono -> 16 kHz preprocessing.

A pure PUBLIC_SYNTHETIC fixture adapter, not a microphone/ASR/voice API.
The source label and caller opt-in are policy declarations, NOT proof of
consent or a cryptographic provenance check. No I/O or optional dependencies.
"""

from dataclasses import dataclass
from hashlib import sha256
import struct

SCHEMA = "mojum.audio.pcm16-resample/1"
TARGET_RATE = 16000
MAX_SECONDS = 10
SOURCE_TAG = "PUBLIC_SYNTHETIC"
_SUPPORTED_RATES = (16000, 32000, 48000)
_Q = 1 << 20

# Fixed-point, unit-sum, symmetrical Hamming-windowed sinc lowpass FIRs.
# Passband for speech up to ~7 kHz, stopband begins >8 kHz. Generated using
# cutoff 7000 Hz, lengths 49 (32k) and 65 (48k), rounded to Q20, residual
# added to center tap. Pinned integers avoid runtime coefficient drift.
_FIR_32000 = (
    1111, 237, -1340, -1015, 1670, 2554, -1522, -5001, 0, 7935,
    3837, -10236, -10610, 10105, 20302, -5175, -32093, -7620,
    44425, 33527, -55311, -89178, 62800, 325740, 458292, 325740,
    62800, -89178, -55311, 33527, 44425, -7620, -32093, -5175,
    20302, 10105, -10610, -10236, 3837, 7935, 0, -5001, -1522,
    2554, 1670, -1015, -1340, 237, 1111,
)
_FIR_48000 = (
    -723, -116, 699, 1139, 685, -635, -1953, -1953, 0, 2857,
    4168, 1965, -3037, -7079, -5903, 1268, 9755, 12027, 3885,
    -10526, -19914, -13979, 6872, 28498, 31261, 5573, -36282,
    -62582, -40256, 41729, 159758, 264185, 305804, 264185,
    159758, 41729, -40256, -62582, -36282, 5573, 31261, 28498,
    6872, -13979, -19914, -10526, 3885, 12027, 9755, 1268,
    -5903, -7079, -3037, 1965, 4168, 2857, 0, -1953, -1953,
    -635, 685, 1139, 699, -116, -723,
)


class PCMResampleError(ValueError):
    """Untrusted/preconditioned input refused before sample conversion."""


@dataclass(frozen=True)
class PCMResampleResult:
    schema: str
    source_tag: str
    source_sample_rate_hz: int
    output_sample_rate_hz: int
    source_frames: int
    output_frames: int
    pcm16le: bytes
    sha256_hex: str


def _rounded_q20(numerator):
    # Round to nearest, half away from zero, exactly in integers.
    if numerator >= 0:
        return (numerator + _Q // 2) // _Q
    return -((-numerator + _Q // 2) // _Q)


def _clamp_pcm16(value):
    return min(32767, max(-32768, value))


def resample_pcm16le_mono(payload, source_rate_hz, *, opt_in=False,
                          source_tag=SOURCE_TAG):
    """Return deterministic 16 kHz PCM16LE for *declared* PUBLIC_SYNTHETIC.

    Exact source rates: 16, 32, 48 kHz. Only complete integer decimation
    durations are accepted; the caller must buffer trailing incomplete frames.
    Requires ``opt_in=True`` and an exact builtin bytes payload. Source audio
    is never read from disk/devices or sent anywhere. No rate guessing,
    mutable global filter state, stochastic rounding or silent tail drop.

    All input (including rate-16k pass-through) is gated before processing.
    Samples beyond the source buffer use edge-hold extension for symmetric FIR
    transients. Output samples start at time zero at 16kHz. This is a complete
    offline clip converter, not a gapless streaming resampler.
    """
    if opt_in is not True:
        raise PCMResampleError("explicit opt_in=True required")
    if type(source_tag) is not str or source_tag != SOURCE_TAG:
        raise PCMResampleError("only declared PUBLIC_SYNTHETIC input allowed")
    if type(source_rate_hz) is not int or source_rate_hz not in _SUPPORTED_RATES:
        raise PCMResampleError("unsupported exact source sample rate")
    if type(payload) is not bytes:
        raise PCMResampleError("PCM payload must be exact bytes")

    # Check bytes BEFORE unpack or FIR allocation. Always cap input to <=10 s.
    if not payload or len(payload) % 2:
        raise PCMResampleError("PCM16LE must contain complete nonempty samples")
    if len(payload) > source_rate_hz * MAX_SECONDS * 2:
        raise PCMResampleError("PCM input duration exceeds ten-second budget")
    count = len(payload) // 2
    factor = source_rate_hz // TARGET_RATE
    if count % factor:
        raise PCMResampleError("source frame count is not 16 kHz aligned")
    out_count = count // factor
    if factor == 1:
        output = payload
    else:
        taps = _FIR_32000 if factor == 2 else _FIR_48000
        radius = len(taps) // 2
        # struct's explicit little-endian format works on big-endian hosts.
        samples = struct.unpack(f"<{count}h", payload)
        result = bytearray(out_count * 2)
        last = count - 1
        for out_index in range(out_count):
            center = out_index * factor
            total = 0
            for tap_index, coefficient in enumerate(taps):
                index = center + tap_index - radius
                if index < 0:
                    index = 0
                elif index > last:
                    index = last
                total += coefficient * samples[index]
            struct.pack_into("<h", result, out_index * 2,
                             _clamp_pcm16(_rounded_q20(total)))
        output = bytes(result)
    return PCMResampleResult(
        schema=SCHEMA,
        source_tag=SOURCE_TAG,
        source_sample_rate_hz=source_rate_hz,
        output_sample_rate_hz=TARGET_RATE,
        source_frames=count,
        output_frames=out_count,
        pcm16le=output,
        sha256_hex=sha256(output).hexdigest(),
    )
