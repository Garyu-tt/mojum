"""Opt-in, public-synthetic-only sherpa-onnx Silero VAD adapter.

It emits *candidate speech intervals* (not transcripts, intent or voice identity).
No microphone, model download, journal write or network integration is provided.
The native optional runtime is imported only after explicit consent and input
validation. Each call creates a disposable VAD, so failures cannot contaminate
future recordings. This module has no non-stdlib import-time dependencies.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import os
from pathlib import Path
import re
import stat

SCHEMA = "mojum.speech.vad-segments/1"
SAMPLE_RATE_HZ = 16_000
WINDOW_SAMPLES = 512
MAX_SECONDS = 10
MAX_SAMPLES = SAMPLE_RATE_HZ * MAX_SECONDS
MAX_SEGMENTS = 64
MAX_MODEL_BYTES = 16 * 1024 * 1024
MAX_TIME_NS = (1 << 63) - 1
PINNED_SHERPA_VERSION = "2.0.3"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _verified_local_model(path: str, expected_sha256: str) -> str:
    """Ensure explicit regular local ONNX path and bounded SHA-256, no symlinks.

    The native library re-opens the path. This is not an OS-level immutable mount
    or a sandbox; see security limitations in the companion document.
    """
    if type(path) is not str or not path or not os.path.isabs(path) or os.path.normpath(path) != path or "\x00" in path:
        raise ValueError("model must be an absolute local path")
    if type(expected_sha256) is not str or _SHA256.fullmatch(expected_sha256) is None:
        raise ValueError("invalid model SHA-256 pin")
    p = Path(path)
    for part in (p, *p.parents):
        try:
            if stat.S_ISLNK(part.lstat().st_mode):
                raise ValueError("model path must not contain symlinks")
        except OSError:
            raise ValueError("model path is not accessible") from None
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            st = os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= MAX_MODEL_BYTES:
                raise ValueError("model must be a bounded regular file")
            h = hashlib.sha256()
            remaining = MAX_MODEL_BYTES + 1
            while remaining:
                block = stream.read(min(65_536, remaining))
                if not block:
                    break
                h.update(block)
                remaining -= len(block)
            if remaining == 0 and stream.read(1):
                raise ValueError("model exceeds size budget")
    except OSError:
        raise ValueError("model cannot be read") from None
    if h.hexdigest() != expected_sha256:
        raise ValueError("model SHA-256 mismatch")
    return path


def _offset_ns(sample_index: int) -> int:
    return (sample_index * 1_000_000_000) // SAMPLE_RATE_HZ


def segment_public_pcm16(
    pcm: bytes,
    *,
    observed_at_ns: int,
    model_path: str,
    model_sha256: str,
    enabled: bool = False,
    consent: bool = False,
    _sherpa=None,
    _numpy=None,
    _backend_version: str | None = None,
) -> dict:
    """Detect speech candidate intervals from a prepared PUBLIC_SYNTHETIC PCM16LE clip.

    Strict 16 kHz mono, 10s, 512-sample native windows. The supplied timestamp
    denotes the FIRST captured sample, in a caller-owned simulated clock.
    No raw samples, features, recording paths or inferred user identity return.

    Private underscore parameters allow isolated fake-backend unit tests only.
    Production callers must omit them. Do not reuse this entrypoint for PRIVATE
    Noosphere samples or live hardware; the only supported scope is synthetic.
    """
    if enabled is not True or consent is not True:
        raise PermissionError("VAD requires explicit opt-in and consent")
    if type(pcm) is not bytes or len(pcm) < WINDOW_SAMPLES * 2 or len(pcm) % 2:
        raise ValueError("expected bounded mono 16-kHz PCM16LE bytes")
    sample_count = len(pcm) // 2
    if sample_count > MAX_SAMPLES:
        raise ValueError("audio exceeds ten-second budget")
    if type(observed_at_ns) is not int or not 0 <= observed_at_ns <= MAX_TIME_NS - _offset_ns(sample_count + WINDOW_SAMPLES):
        raise ValueError("invalid audio timestamp")
    validated_path = _verified_local_model(model_path, model_sha256)

    injected = any(value is not None for value in (_sherpa, _numpy, _backend_version))
    if injected and not all(value is not None for value in (_sherpa, _numpy, _backend_version)):
        raise ValueError("incomplete test-only VAD backend injection")
    if not injected:
        try:
            import numpy as np  # optional native dependency
            import sherpa_onnx as sherpa  # optional native dependency
            version = importlib.metadata.version("sherpa-onnx")
        except (ImportError, importlib.metadata.PackageNotFoundError):
            raise RuntimeError("optional sherpa-onnx 2.0.3 and numpy are required") from None
        backend_name = "sherpa-onnx-silero-vad"
    else:
        sherpa, np = _sherpa, _numpy
        version = _backend_version
        # Injectable test engines must never claim a verified native provenance.
        backend_name = "unverified-test-double-vad"
    if version != PINNED_SHERPA_VERSION:
        raise RuntimeError("unverified sherpa-onnx VAD version")

    # All native calls, including output decoding, are treated as untrusted.
    try:
        config = sherpa.VadModelConfig()
        config.silero_vad.model = validated_path
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = 0.25
        config.silero_vad.min_speech_duration = 0.25
        config.silero_vad.max_speech_duration = 5.0
        config.silero_vad.window_size = WINDOW_SAMPLES
        config.sample_rate = SAMPLE_RATE_HZ
        config.num_threads = 1
        config.provider = "cpu"
        vad = sherpa.VoiceActivityDetector(config, buffer_size_in_seconds=MAX_SECONDS + 2)
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        segments: list[dict] = []
        previous_end = 0
        padded_count = ((sample_count + WINDOW_SAMPLES - 1) // WINDOW_SAMPLES) * WINDOW_SAMPLES

        def drain() -> None:
            nonlocal previous_end
            while not vad.empty():
                if len(segments) >= MAX_SEGMENTS:
                    raise ValueError("VAD segment count exceeded")
                front = vad.front
                start = front.start
                values = np.asarray(front.samples)
                if type(start) is not int or start < previous_end or start >= sample_count:
                    raise ValueError("VAD invalid or nonmonotonic segment")
                if values.ndim != 1 or not 0 < values.size <= MAX_SAMPLES:
                    raise ValueError("VAD invalid segment shape")
                if not bool(np.isfinite(values).all()) or bool(np.any(np.abs(values) > 1.001)):
                    raise ValueError("VAD nonfinite or out-of-range samples")
                untrimmed_end = start + int(values.size)
                if untrimmed_end > padded_count:
                    raise ValueError("VAD segment exceeds input")
                end = min(untrimmed_end, sample_count)
                if start >= end:
                    raise ValueError("VAD empty segment")
                segments.append({
                    "start_sample": start,
                    "end_sample": end,
                    "start_at_ns": observed_at_ns + _offset_ns(start),
                    "end_at_ns": observed_at_ns + _offset_ns(end),
                    "classification": "speech-candidate-not-transcript",
                })
                previous_end = end
                vad.pop()

        for offset in range(0, sample_count, WINDOW_SAMPLES):
            chunk = audio[offset:offset + WINDOW_SAMPLES]
            if chunk.size != WINDOW_SAMPLES:
                chunk = np.pad(chunk, (0, WINDOW_SAMPLES - int(chunk.size)))
            vad.accept_waveform(chunk)
            drain()
        vad.flush()
        drain()
    except Exception:
        # Discard partial output on *any* native fault (or bad native segment).
        raise RuntimeError("VAD backend failed; no observations committed") from None

    return {
        "schema": SCHEMA,
        "source_scope": "PUBLIC_SYNTHETIC",
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "observed_at_ns": observed_at_ns,
        "duration_ns": _offset_ns(sample_count),
        "model_sha256": model_sha256,
        "backend": backend_name,
        "backend_version": PINNED_SHERPA_VERSION,
        "segments": segments,
        "raw_audio_retained": False,
        "transcription_performed": False,
    }
