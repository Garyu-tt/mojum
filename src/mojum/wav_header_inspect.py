"""Bounded, offline RIFF/WAVE PCM16 mono inspector. No sample decoding or I/O."""
from __future__ import annotations

import struct

MAX_WAV_BYTES = 16 * 1024 * 1024
MAX_CHUNKS = 128


class WavFormatError(ValueError):
    """Invalid or unsupported WAV container."""


def inspect_wav(data: bytes) -> dict[str, int | str]:
    """Validate a complete canonical RIFF/WAVE PCM16 mono byte buffer.

    Supports arbitrary bounded ancillary chunks, but exactly one fmt and data.
    Rejects trailing bytes, truncated chunks, RF64, extensible formats, and
    inconsistent sample rates/byte rates. Never returns audio sample content.
    """
    if type(data) is not bytes:
        raise TypeError("data must be exact bytes")
    if not 12 <= len(data) <= MAX_WAV_BYTES:
        raise WavFormatError("WAV size outside budget")
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavFormatError("expected RIFF/WAVE")
    riff_size = struct.unpack_from("<I", data, 4)[0]
    if riff_size != len(data) - 8:
        raise WavFormatError("RIFF length mismatch")
    offset = 12
    fmt = None
    samples = None
    chunks = 0
    while offset < len(data):
        chunks += 1
        if chunks > MAX_CHUNKS:
            raise WavFormatError("chunk count exceeded")
        if len(data) - offset < 8:
            raise WavFormatError("truncated chunk header")
        kind = data[offset:offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        start = offset + 8
        end = start + size
        padded_end = end + (size & 1)
        if padded_end > len(data):
            raise WavFormatError("truncated chunk or padding")
        if size & 1 and data[end] != 0:
            raise WavFormatError("nonzero chunk padding")
        if kind == b"fmt ":
            if fmt is not None or size != 16:
                raise WavFormatError("duplicate or noncanonical fmt")
            audio_format, channels, rate, byte_rate, align, bits = struct.unpack_from(
                "<HHIIHH", data, start
            )
            if (audio_format != 1 or channels != 1 or bits != 16
                    or not 8000 <= rate <= 192000
                    or align != 2 or byte_rate != rate * 2):
                raise WavFormatError("unsupported PCM16 mono fmt")
            fmt = rate
        elif kind == b"data":
            if samples is not None or fmt is None or size % 2:
                raise WavFormatError("duplicate, unordered or unaligned data")
            samples = size // 2
        offset = padded_end
    if fmt is None or samples is None:
        raise WavFormatError("missing fmt or data chunk")
    return {
        "schema": "mojum.wav-inspection/1",
        "sample_rate_hz": fmt,
        "channels": 1,
        "bits_per_sample": 16,
        "sample_count": samples,
        "duration_ms_floor": samples * 1000 // fmt,
        "chunk_count": chunks,
        "container_bytes": len(data),
    }
