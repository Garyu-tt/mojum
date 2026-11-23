"""Explicit opt-in sherpa-onnx offline ASR over PUBLIC synthetic PCM16 WAV only.

No microphone, network, file path, private journal or model loader is accepted.
The installed sherpa-onnx recognizer is supplied by an isolated, trusted caller;
this module only wraps its documented create_stream/decode_stream API.
"""
from __future__ import annotations

from io import BytesIO
import hashlib
import wave
from typing import Any

SCHEMA = "mojum.sherpa-offline-asr/1"
MAX_WAV_BYTES = 400_000
MAX_SAMPLES = 160_000  # <=10 s at 16 kHz, fixed input type
SAMPLE_RATE = 16_000
MAX_TEXT = 500


class ASRBoundaryError(ValueError):
    """Invalid or unapproved sample, or untrusted backend result."""


def _pcm16_from_wav(wav_bytes: bytes) -> bytes:
    if type(wav_bytes) is not bytes or not 44 <= len(wav_bytes) <= MAX_WAV_BYTES:
        raise ASRBoundaryError("WAV byte budget exceeded or invalid input type")
    try:
        with wave.open(BytesIO(wav_bytes), "rb") as reader:
            if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate(),
                    reader.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
                raise ASRBoundaryError("expected 16-kHz mono uncompressed PCM16 WAV")
            count = reader.getnframes()
            if type(count) is not int or not 1 <= count <= MAX_SAMPLES:
                raise ASRBoundaryError("sample duration exceeds budget")
            frames = reader.readframes(count)
            if len(frames) != count * 2:
                raise ASRBoundaryError("truncated PCM16 WAV payload")
            return frames
    except ASRBoundaryError:
        raise
    except (EOFError, wave.Error, OSError, ValueError) as exc:
        raise ASRBoundaryError("invalid WAV document") from exc


def _checked_transcript(raw: Any) -> str:
    if type(raw) is not str or len(raw) > MAX_TEXT:
        raise ASRBoundaryError("invalid transcript length/type")
    if any(ord(c) < 32 or 0x7f <= ord(c) <= 0x9f or
           0xd800 <= ord(c) <= 0xdfff for c in raw):
        raise ASRBoundaryError("transcript contains controls or invalid Unicode")
    try:
        raw.encode("utf-8")
    except UnicodeError as exc:
        raise ASRBoundaryError("invalid transcript encoding") from exc
    return raw


def transcribe_public_wav(wav_bytes: bytes, recognizer: Any, *,
                          enabled: bool = False,
                          visibility: str = "PUBLIC_SYNTHETIC") -> dict[str, Any]:
    """Decode exactly one externally authorized public synthetic fixture.

    Inputs are never written, printed or transmitted. The real optional
    dependency is NumPy (required by sherpa), imported only after opt-in and
    WAV validation. Caller owns and resource-limits the recognizer instance.
    """
    if enabled is not True:
        raise ASRBoundaryError("sherpa-onnx ASR requires explicit opt-in")
    if type(visibility) is not str or visibility != "PUBLIC_SYNTHETIC":
        raise ASRBoundaryError("PRIVATE/user audio not admitted")
    pcm = _pcm16_from_wav(wav_bytes)
    try:
        import numpy as np  # sherpa deployments normally ship NumPy
    except ImportError:
        # Keep the stdlib synthetic fixture truly dependency-free.
        # A real sherpa recognizer normally needs its own NumPy dependency.
        from array import array
        import sys
        signed_samples = array("h")
        signed_samples.frombytes(pcm)
        if sys.byteorder != "little":
            signed_samples.byteswap()
        floats = array("f", (sample / 32768.0 for sample in signed_samples))
    else:
        floats = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        if not np.isfinite(floats).all():
            raise ASRBoundaryError("invalid audio samples")
    # All caller-supplied recognizer methods and properties are untrusted.
    # Do not preserve their exception text or traceback as an outward cause:
    # native backends may embed model paths or tokens in exception messages.
    backend_ok = False
    raw_text = None
    try:
        maker = getattr(recognizer, "create_stream", None)
        decoder = getattr(recognizer, "decode_stream", None)
        if callable(maker) and callable(decoder):
            stream = maker()
            accept = getattr(stream, "accept_waveform", None)
            if callable(accept):
                accept(SAMPLE_RATE, floats)
                decoder(stream)
                raw_text = getattr(getattr(stream, "result", None), "text", None)
                backend_ok = True
    except Exception:
        # Fail closed. Raise *after* this handler, so __context__ is empty
        # and no backend diagnostics can leak through exception chaining.
        pass
    if not backend_ok:
        raise ASRBoundaryError("offline ASR backend failed")
    text = _checked_transcript(raw_text)
    return {"schema": SCHEMA, "source": "sherpa-onnx-offline",
            "visibility": "PUBLIC_SYNTHETIC", "sample_rate": SAMPLE_RATE,
            "frames": len(pcm) // 2, "duration_ms": len(pcm) * 1000 // (2 * SAMPLE_RATE),
            "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
            "transcript": text, "physical_actuation": False,
            "inference": "ASR_OUTPUT_NOT_EVIDENCE_OF_LEARNING"}
