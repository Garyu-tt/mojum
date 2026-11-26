"""Deterministic read-only admission of PUBLIC_SYNTHETIC PCM16 frame timelines.

Validates declared frame/sample-clock alignment, not capture, playback, ASR,
authentication, provenance or consent. Never accesses private journals.
"""
from __future__ import annotations
import hashlib
import json
import re

FRAME_SCHEMA = "mojum.audio.pcm16-frame/1"
WITNESS_SCHEMA = "mojum.audio.frame-timeline-witness/1"
SCOPE = "PUBLIC_SYNTHETIC"
SAMPLE_RATE_HZ = 16000
NS_PER_SAMPLE = 62500
MAX_FRAMES = 256
MAX_NS = (1 << 63) - 1
VALID_FRAME_SAMPLES = frozenset((160, 320, 480))
_FRAME_FIELDS = frozenset((
    "schema", "scope", "session_id", "sequence", "tick", "sample_offset",
    "captured_at_ns", "sample_rate_hz", "channels", "pcm16le",
    "network_access", "physical_actuation",
))
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)


class AudioTimelineError(ValueError):
    """Frame sequence is malformed, untrusted, non-contiguous or over budget."""


def _uint(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_NS:
        raise AudioTimelineError(f"invalid {name}")
    return value


def _session(value: object) -> str:
    if type(value) is not str or len(value) > 64 or _SESSION.fullmatch(value) is None:
        raise AudioTimelineError("invalid session_id")
    return value


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def witness(frames: list[dict] | tuple[dict, ...]) -> dict:
    """Validate 1..256 contiguous 16kHz mono PCM16 frames; return redacted receipt.

    Sequence numbers and sample offsets begin at zero. The initial timestamp
    defines the session origin. Frame timestamps use exact sample-clock
    nanoseconds, not a wall clock. Logical ticks may repeat or increase.
    """
    if type(frames) not in (list, tuple) or not 1 <= len(frames) <= MAX_FRAMES:
        raise AudioTimelineError("frame count outside 1..256")

    session_id = None
    origin_ns = None
    first_tick = None
    previous_tick = None
    next_offset = 0
    chain = hashlib.sha256(b"mojum.audio.frame-timeline-witness/1\n").digest()
    for i, frame in enumerate(frames):
        if type(frame) is not dict or any(type(k) is not str for k in frame):
            raise AudioTimelineError("frame must be a plain string-keyed dict")
        if frame.keys() != _FRAME_FIELDS:
            raise AudioTimelineError("unsupported frame fields")
        if type(frame["schema"]) is not str or frame["schema"] != FRAME_SCHEMA:
            raise AudioTimelineError("unsupported audio frame schema")
        if type(frame["scope"]) is not str or frame["scope"] != SCOPE:
            raise AudioTimelineError("only PUBLIC_SYNTHETIC frames admitted")
        if frame["network_access"] is not False or frame["physical_actuation"] is not False:
            raise AudioTimelineError("network and physical actuation forbidden")
        if type(frame["sample_rate_hz"]) is not int or frame["sample_rate_hz"] != SAMPLE_RATE_HZ:
            raise AudioTimelineError("sample rate must be exactly 16000 Hz")
        if type(frame["channels"]) is not int or frame["channels"] != 1:
            raise AudioTimelineError("only mono PCM16 admitted")
        current_session = _session(frame["session_id"])
        if session_id is None:
            session_id = current_session
        elif current_session != session_id:
            raise AudioTimelineError("session changed mid-stream")
        if _uint(frame["sequence"], "sequence") != i:
            raise AudioTimelineError("non-contiguous frame sequence")
        tick = _uint(frame["tick"], "tick")
        if previous_tick is not None and tick < previous_tick:
            raise AudioTimelineError("logical tick moved backwards")
        if first_tick is None:
            first_tick = tick
        previous_tick = tick
        if _uint(frame["sample_offset"], "sample_offset") != next_offset:
            raise AudioTimelineError("gap or overlap in sample offsets")
        captured_ns = _uint(frame["captured_at_ns"], "captured_at_ns")
        if origin_ns is None:
            origin_ns = captured_ns
        if origin_ns + next_offset * NS_PER_SAMPLE > MAX_NS:
            raise AudioTimelineError("sample clock overflow")
        if captured_ns != origin_ns + next_offset * NS_PER_SAMPLE:
            raise AudioTimelineError("timestamp disagrees with exact sample clock")
        pcm = frame["pcm16le"]
        if type(pcm) is not bytes or len(pcm) not in (320, 640, 960):
            raise AudioTimelineError("frame must be 10/20/30 ms of PCM16LE bytes")
        sample_count = len(pcm) // 2
        next_offset += sample_count
        if origin_ns + next_offset * NS_PER_SAMPLE > MAX_NS:
            raise AudioTimelineError("end timestamp overflow")
        receipt_frame = {
            "session_id": session_id, "sequence": i, "tick": tick,
            "sample_offset": frame["sample_offset"],
            "captured_at_ns": captured_ns, "samples": sample_count,
            "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
        }
        chain = hashlib.sha256(chain + _canonical(receipt_frame)).digest()

    return {
        "schema": WITNESS_SCHEMA, "scope": SCOPE, "session_id": session_id,
        "frames": len(frames), "samples": next_offset,
        "start_ns": origin_ns, "end_ns_exclusive": origin_ns + next_offset * NS_PER_SAMPLE,
        "duration_ns": next_offset * NS_PER_SAMPLE,
        "first_tick": first_tick, "last_tick": previous_tick,
        "chain_sha256": chain.hex(),
        "physical_actuation": False, "network_access": False,
    }
