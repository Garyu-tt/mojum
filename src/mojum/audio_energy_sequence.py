"""Bounded, offline aggregation of PCM16 energy observations.

This module never records audio, detects speech, reads files, or uses a
microphone. Timestamps describe caller-supplied contiguous PCM frames, not
verified acquisition times or consent.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .audio_energy import (
    FRAME_DURATION_NS,
    PCM_BYTES,
    SAMPLE_RATE_HZ,
    measure_pcm16_frame,
)

SCHEMA = "mojum.audio.energy-sequence/1"
MAX_FRAMES = 256
MAX_PCM_BYTES = MAX_FRAMES * PCM_BYTES
MAX_TIME_NS = 2**63 - 1


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    """Stable UTF-8 representation of observations, not original PCM."""
    return (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ) + "\n"
    ).encode("utf-8")


def measure_pcm16_sequence(
    frames: list[bytes] | tuple[bytes, ...],
    *, start_ns: int, clock: str = "simulated",
    origin: str = "synthetic", threshold: float = 0.035,
) -> dict[str, Any]:
    """Measure a bounded contiguous series of 20 ms mono PCM16LE frames.

    Accepts exactly 1..256 whole frames and assigns a caller-supplied
    timestamp to the first frame. Every subsequent timestamp is an exact
    20 ms increment. In a real capture path the caller must independently
    establish continuity, monotonic-clock provenance, and informed consent.

    SHA-256 covers only the returned scalar JSON report (excluding sha256);
    it is neither a PCM fingerprint nor an authenticity/consent proof.
    """
    if type(frames) not in (list, tuple):
        raise ValueError("frames must be a concrete list or tuple")
    # Reject oversized native lists BEFORE allocating a tuple snapshot.
    count = len(frames)
    if not 1 <= count <= MAX_FRAMES:
        raise ValueError("frame count outside 1..256 resource budget")
    # Slice to a hard bound even if another thread extends a mutable list
    # between the length preflight and its immutable snapshot.
    chunks = tuple(frames[:MAX_FRAMES + 1])
    if len(chunks) != count:
        raise ValueError("frame count changed during snapshot")
    if type(start_ns) is not int or not 0 <= start_ns <= MAX_TIME_NS:
        raise ValueError("invalid sequence start timestamp")
    if start_ns > MAX_TIME_NS - (count - 1) * FRAME_DURATION_NS:
        raise ValueError("sequence frame timestamp overflow")
    # Validate the whole sequence before any measurement. bytes are immutable.
    if any(type(chunk) is not bytes or len(chunk) != PCM_BYTES for chunk in chunks):
        raise ValueError("all frames must be exactly 640 native bytes")

    measured = [
        measure_pcm16_frame(
            chunk, observed_at_ns=start_ns + index * FRAME_DURATION_NS,
            clock=clock, origin=origin, threshold=threshold,
        )
        for index, chunk in enumerate(chunks)
    ]
    compact = [
        {
            "observed_at_ns": frame["observed_at_ns"],
            "rms_normalized": frame["rms_normalized"],
            "peak_normalized": frame["peak_normalized"],
            "energy_gate_open": frame["energy_gate_open"],
        }
        for frame in measured
    ]
    # measure_pcm16_frame already validates finite threshold, clock and origin.
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "visibility": "PRIVATE",
        "classification": "energy-only-not-speech",
        "clock": clock,
        "origin": origin,
        "start_ns": start_ns,
        "last_observed_at_ns": compact[-1]["observed_at_ns"],
        "frame_duration_ns": FRAME_DURATION_NS,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "frame_count": count,
        "pcm_bytes_processed": count * PCM_BYTES,
        "threshold_normalized": float(threshold),
        "open_gate_frames": sum(frame["energy_gate_open"] for frame in compact),
        "max_rms_normalized": max(frame["rms_normalized"] for frame in compact),
        "raw_audio_retained": False,
        "frames": compact,
    }
    report["sha256"] = hashlib.sha256(_canonical_bytes(report)).hexdigest()
    return report
