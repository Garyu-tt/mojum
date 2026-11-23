"""Opt-in, bounded adapter contract for caller-supplied local TTS backends.

The module does not load models, access a microphone, play audio, or perform IO.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Iterable, Protocol

REQUEST_SCHEMA = "mojum.audio.tts-request/1"
RESULT_SCHEMA = "mojum.audio.tts-result/1"

ALLOWED_SAMPLE_RATES = (16_000, 24_000, 48_000)
MAX_TEXT_CHARS = 2_000
MAX_AUDIO_MS = 30_000
MAX_CHUNK_BYTES = 64 * 1024
MAX_CHUNKS = 4_096
MAX_BACKEND_ID_CHARS = 64
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_LANG = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*\Z", re.ASCII)


class TTSError(ValueError):
    """Base class for rejected requests, backend output, or adapter failures."""


class TTSBackendError(TTSError):
    """A caller-supplied backend failed while producing PCM chunks."""


class TTSBackend(Protocol):
    """Minimal injected backend interface; implementation must be local-only."""

    backend_id: str
    backend_version: str
    local_only: bool

    def iter_pcm16le(self, request: "TTSRequest") -> Iterable[bytes]:
        """Yield mono signed PCM16LE chunks; do not download models or use a network."""


@dataclass(frozen=True)
class TTSRequest:
    """A consent-gated request for a locally installed preset voice."""

    text: str
    language: str
    voice_id: str
    sample_rate_hz: int = 24_000
    max_audio_ms: int = 10_000
    content_class: str = "USER_PROVIDED"
    consent: bool = False
    schema: str = REQUEST_SCHEMA

    def __post_init__(self) -> None:
        validate_request(self)


@dataclass(frozen=True)
class TTSResult:
    """Detached PCM16LE result and reproducibility metadata."""

    schema: str
    backend_id: str
    backend_version: str
    sample_rate_hz: int
    channels: int
    frames: int
    duration_ms: int
    pcm16le: bytes
    sha256: str


def _exact_text(value: object, *, maximum: int, field: str) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise TTSError(f"invalid {field}")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise TTSError(f"invalid {field} Unicode") from exc
    if not encoded or b"\x00" in encoded:
        raise TTSError(f"invalid {field} encoding")
    return value


def validate_request(request: TTSRequest) -> None:
    """Validate exact native fields before any backend code is invoked."""
    if type(request) is not TTSRequest:
        raise TTSError("request must be an exact TTSRequest")
    if type(request.schema) is not str or request.schema != REQUEST_SCHEMA:
        raise TTSError("unsupported TTS request schema")
    _exact_text(request.text, maximum=MAX_TEXT_CHARS, field="text")
    if not _LANG.fullmatch(_exact_text(request.language, maximum=35, field="language")):
        raise TTSError("invalid language tag")
    if not _ID.fullmatch(_exact_text(request.voice_id, maximum=64, field="voice_id")):
        raise TTSError("invalid local preset voice_id")
    if type(request.sample_rate_hz) is not int or request.sample_rate_hz not in ALLOWED_SAMPLE_RATES:
        raise TTSError("unsupported sample rate")
    if (type(request.max_audio_ms) is not int
            or not 1 <= request.max_audio_ms <= MAX_AUDIO_MS):
        raise TTSError("audio duration budget outside bounds")
    if type(request.content_class) is not str or request.content_class not in (
            "PUBLIC_SYNTHETIC", "USER_PROVIDED"):
        raise TTSError("content must be explicitly classified as public synthetic or user provided")
    if type(request.consent) is not bool or request.consent is not True:
        raise TTSError("explicit TTS consent is required")


def _backend_metadata(backend: object) -> tuple[str, str]:
    try:
        backend_id = getattr(backend, "backend_id")
        backend_version = getattr(backend, "backend_version")
        local_only = getattr(backend, "local_only")
    except Exception as exc:
        raise TTSError("backend is missing required local metadata") from exc
    if type(local_only) is not bool or local_only is not True:
        raise TTSError("backend must explicitly declare local_only=True")
    if type(backend_id) is not str or not _ID.fullmatch(backend_id):
        raise TTSError("invalid backend_id")
    if type(backend_version) is not str or not _ID.fullmatch(backend_version):
        raise TTSError("invalid backend_version")
    return backend_id, backend_version


def _close_iterator(iterator: object) -> None:
    close = getattr(iterator, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def synthesize(request: TTSRequest, backend: TTSBackend) -> TTSResult:
    """Run an injected local backend and enforce incremental output limits.

    The duration limit is enforced while consuming output, so oversized output
    is stopped without first buffering the complete backend response.
    """
    validate_request(request)
    backend_id, backend_version = _backend_metadata(backend)
    producer = getattr(backend, "iter_pcm16le", None)
    if not callable(producer):
        raise TTSError("backend must implement iter_pcm16le(request)")

    try:
        chunks = producer(request)
        iterator = iter(chunks)
    except Exception as exc:
        raise TTSBackendError("backend failed to start") from exc

    max_bytes = request.sample_rate_hz * 2 * request.max_audio_ms // 1_000
    output = bytearray()
    chunk_count = 0
    try:
        while True:
            try:
                chunk = next(iterator)
            except StopIteration:
                break
            except Exception as exc:
                raise TTSBackendError("backend failed while producing PCM") from exc
            chunk_count += 1
            if chunk_count > MAX_CHUNKS:
                raise TTSError("backend exceeded chunk-count budget")
            if type(chunk) is not bytes:
                raise TTSError("backend chunks must be exact bytes")
            if len(chunk) > MAX_CHUNK_BYTES:
                raise TTSError("backend chunk exceeds per-chunk byte budget")
            if len(chunk) % 2:
                raise TTSError("PCM16LE chunk has an incomplete sample")
            if len(output) + len(chunk) > max_bytes:
                raise TTSError("backend exceeded requested audio duration")
            output.extend(chunk)
    except BaseException:
        _close_iterator(iterator)
        raise

    if not output:
        raise TTSError("backend produced no audio frames")
    if len(output) % 2:
        raise TTSError("PCM16LE output has an incomplete sample")

    frames = len(output) // 2
    duration_ms = (frames * 1_000) // request.sample_rate_hz
    if frames <= 0 or duration_ms <= 0 or duration_ms > request.max_audio_ms:
        raise TTSError("backend output duration is invalid")
    pcm = bytes(output)
    return TTSResult(
        schema=RESULT_SCHEMA,
        backend_id=backend_id,
        backend_version=backend_version,
        sample_rate_hz=request.sample_rate_hz,
        channels=1,
        frames=frames,
        duration_ms=duration_ms,
        pcm16le=pcm,
        sha256=hashlib.sha256(pcm).hexdigest(),
    )


__all__ = [
    "ALLOWED_SAMPLE_RATES", "MAX_AUDIO_MS", "MAX_CHUNK_BYTES",
    "MAX_CHUNKS", "MAX_TEXT_CHARS", "REQUEST_SCHEMA", "RESULT_SCHEMA",
    "TTSBackend", "TTSBackendError", "TTSError", "TTSRequest", "TTSResult",
    "synthesize", "validate_request",
]
