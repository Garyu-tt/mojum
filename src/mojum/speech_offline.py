"""Opt-in sherpa-onnx offline ASR observation boundary (#694).

No microphone, network, recording, journal writes, motor commands or backend
imports at module import time. ASR text is untrusted and PRIVATE to the caller.
"""
from __future__ import annotations

import math
import re
from collections.abc import Sequence

SCHEMA = "mojum.sensor.speech-observation/1"
SAMPLE_RATE_HZ = 16_000
MAX_WINDOW_SAMPLES = 5 * SAMPLE_RATE_HZ
MAX_TRANSCRIPT_CHARS = 1024
SILENCE_RMS_THRESHOLD = 0.002
_MAX_CLOCK_MS = 2**53 - 1
_LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\Z")
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class SpeechBridgeError(ValueError):
    """Rejected speech window, backend response, or privacy precondition."""


def _window(samples: Sequence[float], sample_rate_hz: int) -> tuple[list[float], float, int]:
    if type(sample_rate_hz) is not int or sample_rate_hz != SAMPLE_RATE_HZ:
        raise SpeechBridgeError("expected explicit 16000 Hz mono PCM; no implicit resampling")
    if (isinstance(samples, (str, bytes, bytearray))
            or not hasattr(samples, "__len__") or not hasattr(samples, "__iter__")
            or getattr(samples, "ndim", 1) != 1):
        raise SpeechBridgeError("samples must be a bounded one-dimensional sequence")
    count = len(samples)
    if not 1 <= count <= MAX_WINDOW_SAMPLES:
        raise SpeechBridgeError("audio window length outside 1..80000 samples")
    pcm = []
    energy = 0.0
    for sample in samples:
        if isinstance(sample, (str, bytes, bool)):
            raise SpeechBridgeError("invalid PCM sample")
        try:
            number = float(sample)
        except (ValueError, TypeError, OverflowError):
            raise SpeechBridgeError("invalid PCM sample") from None
        if not math.isfinite(number) or not -1.0 <= number <= 1.0:
            raise SpeechBridgeError("PCM must be finite normalized [-1, 1]")
        pcm.append(number)
        energy += number * number
    duration_ms = (count * 1000 + SAMPLE_RATE_HZ - 1) // SAMPLE_RATE_HZ
    return pcm, math.sqrt(energy / count), duration_ms


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise SpeechBridgeError("backend returned non-text transcript")
    transcript = value.strip()
    if len(transcript) > MAX_TRANSCRIPT_CHARS or any(
        ord(ch) < 32 or ord(ch) == 127 for ch in transcript
    ):
        raise SpeechBridgeError("unsafe or oversized ASR transcript")
    return transcript


class SherpaOnnxOffline:
    """Use an explicitly caller-created sherpa_onnx.OfflineRecognizer.

    waveform_factory is only for stdlib fake-backend tests; production uses
    a lazy numpy float32 conversion compatible with sherpa's Python binding.
    """

    def __init__(self, recognizer: object, *, waveform_factory=None):
        if recognizer is None:
            raise SpeechBridgeError("explicit offline recognizer required")
        if waveform_factory is not None and not callable(waveform_factory):
            raise SpeechBridgeError("invalid waveform factory")
        self._recognizer = recognizer
        self._waveform_factory = waveform_factory

    def decode(self, pcm: list[float]) -> str:
        try:
            if self._waveform_factory is None:
                import numpy as np  # Optional dependency, loaded only upon decode.
                waveform = np.asarray(pcm, dtype=np.float32)
            else:
                waveform = self._waveform_factory(pcm)
            stream = self._recognizer.create_stream()
            stream.accept_waveform(SAMPLE_RATE_HZ, waveform)
            self._recognizer.decode_stream(stream)
            return stream.result.text
        except Exception:
            # Never expose model paths, raw audio, or provider exception strings.
            raise SpeechBridgeError("offline ASR backend failed") from None


class OfflineSpeechSession:
    """Bounded ordered final-window ASR; no continuous microphone stream.

    Explicit consent is required for each window. A failed decode leaves the
    cursor unchanged. Returned events do NOT enter the canonical Aquarium or
    Noosphere without a separate reviewed consumer and authorization.
    """

    def __init__(self, backend: object, *, model_id: str, enabled: bool = False):
        if type(enabled) is not bool or (enabled and backend is None):
            raise SpeechBridgeError("invalid opt-in backend configuration")
        if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
            raise SpeechBridgeError("model_id must be opaque, not a path or secret")
        self._backend = backend
        self._enabled = enabled
        self._model_id = model_id
        self._end_ms: int | None = None
        self._sequence = 0

    def transcribe(
        self,
        samples: Sequence[float],
        *,
        sample_rate_hz: int,
        start_ms: int,
        language_hint: str,
        consent: bool = False,
    ) -> dict:
        if not self._enabled:
            raise SpeechBridgeError("speech adapter disabled by default")
        if consent is not True:
            raise SpeechBridgeError("per-window consent required")
        if type(start_ms) is not int or not 0 <= start_ms <= _MAX_CLOCK_MS:
            raise SpeechBridgeError("invalid monotonic relative timestamp")
        if not isinstance(language_hint, str) or not _LANGUAGE.fullmatch(language_hint):
            raise SpeechBridgeError("invalid explicit language hint")
        if self._end_ms is not None and start_ms < self._end_ms:
            raise SpeechBridgeError("overlapping or reordered audio window")
        pcm, rms, duration_ms = _window(samples, sample_rate_hz)
        end_ms = start_ms + duration_ms
        if end_ms > _MAX_CLOCK_MS:
            raise SpeechBridgeError("audio timeline overflow")
        if rms < SILENCE_RMS_THRESHOLD:
            text = ""
            status = "silence"
        else:
            try:
                value = self._backend.decode(pcm)
            except SpeechBridgeError:
                raise
            except Exception:
                raise SpeechBridgeError("offline ASR backend failed") from None
            text = _text(value)
            status = "recognized" if text else "no_speech"
        result = {
            "schema": SCHEMA,
            "backend": "sherpa_onnx.offline",
            "model_id": self._model_id,
            "sequence": self._sequence,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "language_hint": language_hint,
            "text": text,
            "status": status,
            "is_final": True,
            "trust": "untrusted_observation_not_instruction",
            "privacy": "PRIVATE_not_persisted",
        }
        self._end_ms = end_ms
        self._sequence += 1
        return result
