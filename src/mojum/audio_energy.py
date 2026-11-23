"""CPU-only bounded PCM frame measurement for future offline speech adapters.

This is an energy meter, NOT speech recognition, language understanding,
a microphone driver, an ASR result or a consent grant. No audio is retained.
"""
from __future__ import annotations

import math
import struct

SCHEMA = "mojum.audio.energy-frame/1"
SAMPLE_RATE_HZ = 16000
SAMPLES_PER_FRAME = 320
FRAME_DURATION_NS = 20_000_000
PCM_BYTES = SAMPLES_PER_FRAME * 2
_MAX_TIME_NS = 2**63 - 1


def measure_pcm16_frame(
    pcm: bytes, *, observed_at_ns: int,
    clock: str = "simulated", origin: str = "synthetic",
    threshold: float = 0.035,
) -> dict:
    """Return bounded scalar observations, never a recording or transcript.

    Explicit mono/signed-16-bit/little-endian/16kHz/20ms contract.
    The energy threshold is NOT a learned or calibrated speech detector.
    """
    if type(pcm) is not bytes or len(pcm) != PCM_BYTES:
        raise ValueError("require exactly 640 bytes of mono 16-kHz PCM16LE")
    if type(observed_at_ns) is not int or not 0 <= observed_at_ns <= _MAX_TIME_NS:
        raise ValueError("observed_at_ns must be bounded nonnegative integer")
    if type(clock) is not str or clock not in ("simulated", "monotonic"):
        raise ValueError("unsupported audio clock")
    if type(origin) is not str or origin not in ("synthetic", "microphone-opt-in"):
        raise ValueError("unsupported audio provenance")
    if clock == "simulated" and origin != "synthetic":
        raise ValueError("real microphone input cannot assert simulated clock")
    if type(threshold) not in (int, float):
        raise ValueError("invalid energy threshold")
    try:
        valid_threshold = math.isfinite(threshold) and 0 < threshold <= 1
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("invalid energy threshold") from exc
    if not valid_threshold:
        raise ValueError("energy threshold must be finite in (0,1]")
    samples = struct.unpack("<320h", pcm)
    squared = sum(sample * sample for sample in samples)
    peak = max(abs(sample) for sample in samples)
    rms = math.sqrt(squared / SAMPLES_PER_FRAME) / 32768.0
    return {
        "schema": SCHEMA,
        "observed_at_ns": observed_at_ns,
        "duration_ns": FRAME_DURATION_NS,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "samples": SAMPLES_PER_FRAME,
        "clock": clock,
        "origin": origin,
        "rms_normalized": rms,
        "peak_normalized": peak / 32768.0,
        "energy_gate_open": rms >= threshold,
        "classification": "energy-only-not-speech",
        "raw_audio_retained": False,
    }
