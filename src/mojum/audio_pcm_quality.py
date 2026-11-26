"""Opt-in, bounded diagnostics for synthetic 16-kHz mono PCM16LE.

This is not speech detection, a microphone adapter, an audio archive, or a
provenance verifier. It deliberately returns scalar diagnostics only.
"""
from __future__ import annotations

import struct

SCHEMA = "mojum.audio.pcm-quality/1"
SAMPLE_RATE_HZ = 16_000
FRAME_SAMPLES = 320  # 20 ms, compatible with the existing energy frame.
FRAME_BYTES = FRAME_SAMPLES * 2
MAX_FRAMES = 500  # 10 seconds maximum, before any decoding/allocation.
MAX_BYTES = FRAME_BYTES * MAX_FRAMES
DC_THRESHOLD_PERCENT = 2
CLIPPING_RUN_SAMPLES = 3


def _nearest_lsb(total: int, n: int) -> int:
    """Signed nearest integer, exact with half ties away from zero."""
    magnitude, remainder = divmod(abs(total), n)
    if remainder * 2 >= n:
        magnitude += 1
    return -magnitude if total < 0 else magnitude


def analyze_pcm16_quality(
    pcm: bytes,
    *,
    sample_rate_hz: int,
    origin: str,
    enabled: bool = False,
) -> dict:
    """Inspect finite caller-asserted PUBLIC_SYNTHETIC PCM, without retaining it.

    Requiring an explicit opt-in is a guard against accidental use as an
    always-on microphone pipeline. Caller provenance is asserted, not proven.
    The flags are diagnostic *candidates*, never proof of recording faults.
    """
    if type(enabled) is not bool or not enabled:
        raise ValueError("explicit quality diagnostic opt-in required")
    if type(origin) is not str or origin != "PUBLIC_SYNTHETIC":
        raise ValueError("only asserted PUBLIC_SYNTHETIC input is permitted")
    if type(sample_rate_hz) is not int or sample_rate_hz != SAMPLE_RATE_HZ:
        raise ValueError("require explicit 16000-Hz sample rate")
    if type(pcm) is not bytes:
        raise ValueError("require exact immutable PCM bytes")
    size = len(pcm)
    if size == 0 or size > MAX_BYTES or size % FRAME_BYTES:
        raise ValueError("PCM must contain 1..500 complete 20-ms mono frames")

    samples_count = size // 2
    minimum = 32767
    maximum = -32768
    total = 0
    rail_hits = 0
    rail_run = 0
    longest_rail_run = 0
    previous_rail = None
    previous_sign = 0
    crossings = 0
    first_sample = None
    flatline = True

    for (sample,) in struct.iter_unpack("<h", pcm):
        minimum = min(minimum, sample)
        maximum = max(maximum, sample)
        total += sample
        if first_sample is None:
            first_sample = sample
        elif sample != first_sample:
            flatline = False

        if sample in (-32768, 32767):
            rail_hits += 1
            rail_run = rail_run + 1 if sample == previous_rail else 1
            longest_rail_run = max(longest_rail_run, rail_run)
            previous_rail = sample
        else:
            rail_run = 0
            previous_rail = None

        sign = (sample > 0) - (sample < 0)
        if sign:
            if previous_sign and sign != previous_sign:
                crossings += 1
            previous_sign = sign

    silence = minimum == 0 and maximum == 0
    dc_candidate = abs(total) * 100 >= samples_count * 32768 * DC_THRESHOLD_PERCENT
    clipping_candidate = longest_rail_run >= CLIPPING_RUN_SAMPLES
    flags = []
    if silence:
        flags.append("digital_silence")
    elif flatline:
        flags.append("flatline")
    if dc_candidate:
        flags.append("possible_dc_offset")
    if clipping_candidate:
        flags.append("possible_hard_clipping")

    return {
        "schema": SCHEMA,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "origin": origin,
        "sample_count": samples_count,
        "frame_count": size // FRAME_BYTES,
        "duration_ns": samples_count * 62_500,
        "min_sample": minimum,
        "max_sample": maximum,
        "dc_mean_lsb": _nearest_lsb(total, samples_count),
        "rail_hit_count": rail_hits,
        "longest_identical_rail_run": longest_rail_run,
        "zero_crossings": crossings,
        "flags": flags,
        "classification": "signal-diagnostics-only-not-speech",
        "raw_audio_retained": False,
    }
