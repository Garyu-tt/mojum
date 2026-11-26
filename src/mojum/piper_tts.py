"""Off-by-default, public-synthetic-only Piper text-to-WAV adapter.

No bundled engine/voices, no microphone, no audio playback, no journal access.
The native process is NOT a security sandbox; OS-level isolation is required.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unicodedata
import wave

SCHEMA = "mojum.speech.piper-output/1"
MAX_TEXT_BYTES = 2048
MAX_WAV_BYTES = 1_200_000
MAX_SECONDS = 12
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}(?:[a-zA-Z0-9.+-]{0,24})?\Z")


@dataclass(frozen=True)
class PiperProfile:
    """Explicitly provided, locally preinstalled runtime and voice pins."""
    python_executable: str
    python_sha256: str
    model_onnx: str
    model_sha256: str
    config_sha256: str
    expected_piper_version: str
    voice_license_reviewed: bool = False


@dataclass(frozen=True)
class SynthesizedWav:
    schema: str
    wav: bytes
    wav_sha256: str
    sample_rate_hz: int
    sample_frames: int
    duration_ms: int
    backend: str
    backend_version: str
    model_sha256: str
    elapsed_ms: int
    provenance: str = "PUBLIC_SYNTHETIC"
    audio_played: bool = False


def _pin_file(raw: str, digest: str, *, suffix: str | None = None,
              interpreter: bool = False) -> Path:
    if type(raw) is not str or not raw or "\x00" in raw:
        raise ValueError("invalid pinned path")
    if type(digest) is not str or not _HEX.fullmatch(digest):
        raise ValueError("invalid SHA-256 pin")
    path = Path(raw)
    if not path.is_absolute() or (suffix and not raw.endswith(suffix)):
        raise ValueError("pinned path must be absolute and have expected suffix")
    # Exclude symlinks in the whole ancestry, not just the final component.
    try:
        if (not interpreter and path != path.resolve(strict=True)) or not path.is_file():
            raise ValueError("pinned path is not a canonical regular file")
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
    except OSError as exc:
        raise ValueError("pinned asset unreadable") from exc
    if h.hexdigest() != digest:
        raise ValueError("pinned asset hash mismatch")
    return path


def _public_text(text: str) -> None:
    if type(text) is not str or not text.strip() or text != unicodedata.normalize("NFC", text):
        raise ValueError("require nonempty NFC public text")
    try:
        encoded = text.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise ValueError("require valid UTF-8 text") from exc
    if len(encoded) > MAX_TEXT_BYTES or "[[" in text or "]]" in text:
        raise ValueError("text exceeds limits or contains phoneme markup")
    # Cover C0/C1 controls plus Unicode line/paragraph separators, which
    # Python str.splitlines() also treats as line boundaries.
    if any(unicodedata.category(ch) in {"Cc", "Zl", "Zp"} for ch in text):
        raise ValueError("text contains control characters")


def _read_wav(path: Path) -> tuple[bytes, int, int]:
    try:
        size = path.stat().st_size
        if size < 46 or size > MAX_WAV_BYTES or path.is_symlink() or not path.is_file():
            raise ValueError("Piper WAV size/type rejected")
        data = path.read_bytes()
        if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            raise ValueError("Piper output is not RIFF/WAVE")
        if int.from_bytes(data[4:8], "little") + 8 != len(data):
            raise ValueError("Piper WAV RIFF length mismatch")
        with wave.open(io.BytesIO(data), "rb") as wav:
            rate = wav.getframerate()
            frames = wav.getnframes()
            if (wav.getnchannels() != 1 or wav.getsampwidth() != 2
                    or wav.getcomptype() != "NONE" or not 8000 <= rate <= 48000
                    or not 0 < frames <= rate * MAX_SECONDS):
                raise ValueError("Piper WAV encoding/duration rejected")
            if len(wav.readframes(frames)) != frames * 2:
                raise ValueError("Piper WAV truncated")
    except (OSError, EOFError, wave.Error, OverflowError) as exc:
        raise ValueError("invalid Piper WAV") from exc
    return data, rate, frames


def synthesize_public_wav(
    text: str,
    profile: PiperProfile,
    *,
    enabled: bool = False,
    provenance: str = "PUBLIC_SYNTHETIC",
    timeout_seconds: int = 15,
    cpu_threads: int = 1,
) -> SynthesizedWav:
    """Run an installed Piper CLI and return a bounded WAV without playing it.

    Only PUBLIC_SYNTHETIC text is allowed. Caller must ensure that assertion is true.
    Piper and model binaries execute with caller privileges, not a full sandbox.
    """
    if (enabled is not True or type(provenance) is not str
            or provenance != "PUBLIC_SYNTHETIC"):
        raise PermissionError("Piper is opt-in and PUBLIC_SYNTHETIC only")
    _public_text(text)
    if not isinstance(profile, PiperProfile) or profile.voice_license_reviewed is not True:
        raise PermissionError("verified voice provenance/license review required")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
        raise ValueError("invalid wall timeout")
    if type(cpu_threads) is not int or not 1 <= cpu_threads <= 4:
        raise ValueError("invalid CPU thread budget")
    if (type(profile.expected_piper_version) is not str
            or not _VERSION.fullmatch(profile.expected_piper_version)):
        raise ValueError("invalid version pin")
    python = _pin_file(profile.python_executable, profile.python_sha256, interpreter=True)
    model = _pin_file(profile.model_onnx, profile.model_sha256, suffix=".onnx")
    config = _pin_file(str(model) + ".json", profile.config_sha256, suffix=".onnx.json")
    if config.parent != model.parent:
        raise ValueError("model/config directory mismatch")

    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="mojum-piper-") as dirname:
        root = Path(dirname)
        os.chmod(root, 0o700)
        source = root / "text.txt"
        target = root / "result.wav"
        # Private file, no text in argv, environ, stdout, stderr or logs.
        with source.open("x", encoding="utf-8") as out:
            os.chmod(source, 0o600)
            out.write(text + "\n")
        safe_env = {
            "HOME": dirname, "XDG_CACHE_HOME": dirname, "TMPDIR": dirname,
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "OMP_NUM_THREADS": str(cpu_threads),
            "OPENBLAS_NUM_THREADS": str(cpu_threads),
            "MKL_NUM_THREADS": str(cpu_threads),
        }
        def run(args: list[str]) -> subprocess.CompletedProcess:
            try:
                return subprocess.run(
                    args, cwd=root, env=safe_env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=timeout_seconds, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError("Piper process unavailable or timed out") from exc

        # Compare inside the pinned child interpreter. Do not capture unbounded
        # stdout into parent RAM: a faulty executable may print arbitrary data.
        version_check = (
            "import importlib.metadata as m, sys;"
            "sys.exit(0 if m.version('piper-tts') == sys.argv[1] else 1)"
        )
        check = run([str(python), "-c", version_check,
                     profile.expected_piper_version])
        if check.returncode != 0:
            raise RuntimeError("Piper installed package version mismatch")
        completed = run([
            str(python), "-m", "piper", "-m", str(model),
            "-f", str(target), "--input-file", str(source),
        ])
        if completed.returncode != 0:
            raise RuntimeError("Piper synthesis failed")
        data, rate, frames = _read_wav(target)
    return SynthesizedWav(
        schema=SCHEMA, wav=data, wav_sha256=hashlib.sha256(data).hexdigest(),
        sample_rate_hz=rate, sample_frames=frames,
        duration_ms=frames * 1000 // rate, backend="OHF-Voice/piper1-gpl",
        backend_version=profile.expected_piper_version,
        model_sha256=profile.model_sha256,
        elapsed_ms=int((time.monotonic() - start) * 1000),
    )
