"""Opt-in, offline whisper.cpp CLI comparator for PUBLIC synthetic WAV fixtures.

Never records audio, downloads models, or connects to the Mojum organism.
The subprocess is trusted external code and MUST be OS-sandboxed by its operator.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
import unicodedata
import wave

AUDIO_SCHEMA = "mojum.eval.synthetic-public-wav/1"
RESULT_SCHEMA = "mojum.eval.whispercpp-offline-asr/1"
MAX_SECONDS = 10
SAMPLE_RATE = 16000
MAX_WAV_BYTES = SAMPLE_RATE * 2 * MAX_SECONDS + 4096
MAX_RESULT_BYTES = 4096
MAX_EXECUTABLE_BYTES = 128 * 1024 * 1024
MAX_MODEL_BYTES = 1024 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REVISION = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_MANIFEST_KEYS = {"schema", "visibility", "origin", "clip_id", "opt_in"}
_LANGUAGES = frozenset({"en", "ru", "uk"})


class WhisperCPPProbeError(ValueError):
    """Untrusted fixture, unsupported backend, budget, or inference failure."""


def _manifest(value):
    if type(value) is not dict or set(value) != _MANIFEST_KEYS:
        raise WhisperCPPProbeError("exact synthetic audio manifest required")
    if (any(type(value[key]) is not str for key in ("schema", "visibility", "origin"))
            or value["schema"] != AUDIO_SCHEMA
            or value["visibility"] != "PUBLIC"
            or value["origin"] != "synthetic_fixture"
            or value["opt_in"] is not True):
        raise WhisperCPPProbeError("only explicitly opted-in PUBLIC synthetic fixtures allowed")
    if type(value["clip_id"]) is not str or _ID.fullmatch(value["clip_id"]) is None:
        raise WhisperCPPProbeError("invalid bounded clip identifier")
    return value["clip_id"]


def _validated_wav(blob):
    if type(blob) is not bytes or not 44 <= len(blob) <= MAX_WAV_BYTES:
        raise WhisperCPPProbeError("WAV byte limit exceeded or invalid bytes")
    if blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise WhisperCPPProbeError("RIFF/WAVE header required")
    if int.from_bytes(blob[4:8], "little") != len(blob) - 8:
        raise WhisperCPPProbeError("RIFF declared size mismatch")
    try:
        with wave.open(io.BytesIO(blob), "rb") as audio:
            frames = audio.getnframes()
            if (audio.getcomptype() != "NONE" or audio.getnchannels() != 1
                    or audio.getsampwidth() != 2 or audio.getframerate() != SAMPLE_RATE
                    or not 1 <= frames <= SAMPLE_RATE * MAX_SECONDS):
                raise WhisperCPPProbeError("requires mono 16 kHz PCM16, 1..10 seconds")
            if len(audio.readframes(frames)) != frames * 2:
                raise WhisperCPPProbeError("truncated WAV payload")
    except (EOFError, wave.Error, ValueError) as exc:
        if isinstance(exc, WhisperCPPProbeError):
            raise
        raise WhisperCPPProbeError("invalid PCM16 WAV structure") from exc
    return frames


def _stage_verified_file(path, expected_hash, *, limit, executable, destination):
    """Pin source *bytes* into an owner-private file before process execution.

    A verified source pathname is not stable: rename/replace between hashing
    and spawn would silently run different code or weights. The child receives
    only these SHA-verified staged paths, not the caller's mutable paths.
    """
    if type(expected_hash) is not str or _HASH.fullmatch(expected_hash) is None:
        raise WhisperCPPProbeError("explicit lowercase SHA-256 pin required")
    if not isinstance(path, (str, Path)):
        raise WhisperCPPProbeError("explicit absolute backend path required")
    raw = Path(path)
    if not raw.is_absolute() or raw.is_symlink():
        raise WhisperCPPProbeError("backend paths must be absolute and not symlinks")
    destination = Path(destination)
    mode = 0o700 if executable else 0o600
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(raw, flags)
        with os.fdopen(fd, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or not 1 <= metadata.st_size <= limit:
                raise WhisperCPPProbeError("backend file is not a bounded regular file")
            if executable and (not (metadata.st_mode & 0o111) or not os.access(raw, os.X_OK)):
                raise WhisperCPPProbeError("backend binary is not executable")
            output_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            digest = hashlib.sha256()
            copied = 0
            with os.fdopen(output_fd, "wb") as output:
                while chunk := source.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > limit:
                        raise WhisperCPPProbeError("backend staged file exceeds byte budget")
                    digest.update(chunk)
                    output.write(chunk)
            if copied != metadata.st_size or digest.hexdigest() != expected_hash:
                raise WhisperCPPProbeError("backend SHA-256 mismatch")
        destination.chmod(mode)
    except OSError as exc:
        raise WhisperCPPProbeError("backend path or private staging unavailable") from exc
    return str(destination)

def _clean_transcript(raw):
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise WhisperCPPProbeError("invalid UTF-8 transcript") from exc
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Cs") and ch not in "\r\n\t"
           for ch in text):
        raise WhisperCPPProbeError("unsafe transcript control character")
    # Canonicalize only whitespace, without fabricating words or timestamps.
    return " ".join(text.split())


def compare_transcript(reference, observed):
    """Bounded word error rate vs a PUBLIC frozen synthetic reference.

    No reference/transcript content is retained in the returned score.
    """
    for value in (reference, observed):
        if type(value) is not str or len(value) > MAX_RESULT_BYTES:
            raise WhisperCPPProbeError("invalid transcript comparison length")
        if any(unicodedata.category(ch) in ("Cc", "Cf", "Cs") and ch not in "\r\n\t"
               for ch in value):
            raise WhisperCPPProbeError("unsafe transcript comparison character")
    a, b = reference.casefold().split(), observed.casefold().split()
    if not 1 <= len(a) <= 128 or len(b) > 128:
        raise WhisperCPPProbeError("reference/observation word budget exceeded")
    prev = list(range(len(b) + 1))
    for i, token in enumerate(a, 1):
        curr = [i]
        for j, guess in enumerate(b, 1):
            curr.append(min(curr[-1] + 1, prev[j] + 1,
                            prev[j - 1] + (token != guess)))
        prev = curr
    return {"reference_words": len(a), "edit_distance": prev[-1],
            "word_error_rate": prev[-1] / len(a)}


def run_whispercpp_probe(
    wav_bytes, manifest, *, enabled=False, executable_path=None,
    model_path=None, executable_sha256=None, model_sha256=None,
    backend_revision=None, language="en", threads=2, timeout_s=30,
    frozen_reference=None,
):
    """Run a *trusted, pinned* local whisper-cli on synthetic fixture bytes.

    Caller must explicitly opt in and provide local paths/SHA-256 pins; paths
    and raw audio are never returned. Backend itself is not a security sandbox.
    A result is an ASR observation only, never a motor command or school grade.
    """
    if enabled is not True:
        raise WhisperCPPProbeError("external process disabled; explicit enabled=True required")
    clip_id = _manifest(manifest)
    frame_count = _validated_wav(wav_bytes)
    if type(backend_revision) is not str or _REVISION.fullmatch(backend_revision) is None:
        raise WhisperCPPProbeError("pin whisper.cpp source to a 40-hex commit revision")
    if type(language) is not str or language not in _LANGUAGES:
        raise WhisperCPPProbeError("unsupported pinned language")
    if type(threads) is not int or not 1 <= threads <= 8:
        raise WhisperCPPProbeError("threads must be integer 1..8")
    if type(timeout_s) not in (float, int) or not math.isfinite(timeout_s) or not 1 <= timeout_s <= 120:
        raise WhisperCPPProbeError("timeout must be finite 1..120 seconds")
    if frozen_reference is not None:
        compare_transcript(frozen_reference, "")
    with tempfile.TemporaryDirectory(prefix="mojum-whisper-synthetic-") as tmp:
        os.chmod(tmp, 0o700)
        executable = _stage_verified_file(
            executable_path, executable_sha256, limit=MAX_EXECUTABLE_BYTES,
            executable=True, destination=Path(tmp) / "whisper-cli",
        )
        model = _stage_verified_file(
            model_path, model_sha256, limit=MAX_MODEL_BYTES,
            executable=False, destination=Path(tmp) / "model.bin",
        )
        started = time.monotonic()
        audio_path = Path(tmp) / "public-synthetic.wav"
        fd = os.open(audio_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(wav_bytes)
        output_prefix = Path(tmp) / "transcript"
        args = [executable, "-m", model, "-f", str(audio_path), "-l", language,
                "-t", str(threads), "-p", "1", "-ng", "-nt", "-otxt",
                "-of", str(output_prefix), "-np"]
        # Keep credentials/proxies out of the child environment. This does not
        # replace a network-denying OS sandbox around the trusted binary.
        child_env = {"HOME": tmp, "TMPDIR": tmp, "LANG": "C", "LC_ALL": "C"}
        try:
            completed = subprocess.run(
                args, cwd=tmp, env=child_env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=timeout_s, check=False, close_fds=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WhisperCPPProbeError("local whisper.cpp runner failed or timed out") from exc
        if completed.returncode != 0:
            raise WhisperCPPProbeError("local whisper.cpp runner returned nonzero exit")
        transcript_path = Path(str(output_prefix) + ".txt")
        try:
            if transcript_path.is_symlink() or not transcript_path.is_file():
                raise WhisperCPPProbeError("backend transcript file missing or unsafe")
            if transcript_path.stat().st_size > MAX_RESULT_BYTES:
                raise WhisperCPPProbeError("backend transcript byte budget exceeded")
            with transcript_path.open("rb") as stream:
                raw = stream.read(MAX_RESULT_BYTES + 1)
        except OSError as exc:
            raise WhisperCPPProbeError("cannot read backend transcript") from exc
        if len(raw) > MAX_RESULT_BYTES:
            raise WhisperCPPProbeError("backend transcript byte budget exceeded")
        transcript = _clean_transcript(raw)
    result = {
        "schema": RESULT_SCHEMA,
        "source": "opt_in_local_whispercpp_public_synthetic_only",
        "clip_id": clip_id,
        "audio_sha256": hashlib.sha256(wav_bytes).hexdigest(),
        "model_sha256": model_sha256,
        "executable_sha256": executable_sha256,
        "backend_revision_claim": backend_revision,
        "language": language,
        "frames": frame_count,
        "duration_ms": frame_count * 1000 // SAMPLE_RATE,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        "transcript": transcript,
        "motor_action": None,
    }
    if frozen_reference is not None:
        result["frozen_reference_score"] = compare_transcript(frozen_reference, transcript)
    return result
