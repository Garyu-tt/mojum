"""Opt-in offline Mido SMF to public, read-only music-school score projection.

No MIDI ports, audio playback, learner mutation, Noosphere/SQLite or network.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
import re
import struct
from typing import Any

SCHEMA = "mojum.music-midi-score/1"
MIDO_VERSION = "1.3.3"
MAX_MIDI_BYTES = 65_536
MAX_TRACKS = 16
MAX_MESSAGES = 4096
MAX_NOTES = 1024
MAX_TEMPOS = 128
MAX_TICKS = 5_000_000
MAX_DURATION_US = 600_000_000
MAX_TICKS_PER_BEAT = 9600
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SPDX = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}\Z", re.ASCII)


class MidiScoreError(ValueError):
    """Invalid, unsupported or non-opted-in MIDI score input."""


def _integer(value: Any, lower: int, upper: int, field: str) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise MidiScoreError(f"invalid {field}")
    return value


def _time_us(value: Fraction) -> int:
    """Deterministic nearest microsecond, round-half-up, no float drift."""
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _source(sha256: str, source_kind: str, license_id: str | None) -> None:
    if type(sha256) is not str or _SHA256.fullmatch(sha256) is None:
        raise MidiScoreError("invalid exact source SHA-256")
    if source_kind == "PUBLIC_SYNTHETIC":
        if license_id is not None:
            raise MidiScoreError("synthetic input must not assert an external license")
    elif source_kind == "PUBLIC_LICENSED":
        if type(license_id) is not str or _SPDX.fullmatch(license_id) is None:
            raise MidiScoreError("licensed material requires a bounded SPDX identifier")
    else:
        raise MidiScoreError("PRIVATE, unknown or unverified MIDI source denied")


def _preflight(blob: bytes) -> tuple[int, int]:
    """Validate complete MIDI chunk framing before allocating the parser."""
    if type(blob) is not bytes or not 14 <= len(blob) <= MAX_MIDI_BYTES:
        raise MidiScoreError("MIDI bytes missing, wrong type or over budget")
    if blob[:4] != b"MThd" or blob[4:8] != b"\x00\x00\x00\x06":
        raise MidiScoreError("unsupported MIDI header")
    fmt, ntracks, division = struct.unpack(">HHH", blob[8:14])
    if fmt not in (0, 1) or not 1 <= ntracks <= MAX_TRACKS:
        raise MidiScoreError("unsupported MIDI format or track count")
    if fmt == 0 and ntracks != 1:
        raise MidiScoreError("MIDI type 0 must have one track")
    if not 1 <= division <= MAX_TICKS_PER_BEAT:
        raise MidiScoreError("SMPTE and invalid tick division not supported")
    offset = 14
    for _ in range(ntracks):
        if offset + 8 > len(blob) or blob[offset:offset + 4] != b"MTrk":
            raise MidiScoreError("invalid MIDI track chunk")
        size = int.from_bytes(blob[offset + 4:offset + 8], "big")
        offset += 8 + size
        if size == 0 or offset > len(blob):
            raise MidiScoreError("invalid MIDI track length")
    if offset != len(blob):
        raise MidiScoreError("unparsed MIDI bytes forbidden")
    return ntracks, division


def normalize_tracks(
    tracks: Any, *, ticks_per_beat: int, source_sha256: str,
    source_kind: str, license_id: str | None = None,
) -> dict[str, Any]:
    """Pure-stdlib deterministic normalization of Mido-shaped track lists.

    Caller must independently verify provenance, licenses and public scope.
    This does not open files/ports or write learner/brain/Noosphere state.
    """
    _source(source_sha256, source_kind, license_id)
    _integer(ticks_per_beat, 1, MAX_TICKS_PER_BEAT, "ticks_per_beat")
    if type(tracks) not in (tuple, list) or not 1 <= len(tracks) <= MAX_TRACKS:
        raise MidiScoreError("invalid decoded track collection")
    events: list[tuple[int, int, int, Any]] = []
    messages = 0
    for track_idx, track in enumerate(tracks):
        if type(track) not in (list, tuple) and not isinstance(track, list):
            raise MidiScoreError("decoded track must be a bounded list")
        tick = 0
        for msg_idx, msg in enumerate(track):
            messages += 1
            if messages > MAX_MESSAGES:
                raise MidiScoreError("MIDI message budget exceeded")
            tick += _integer(getattr(msg, "time", None), 0, MAX_TICKS, "delta tick")
            if tick > MAX_TICKS:
                raise MidiScoreError("MIDI tick budget exceeded")
            events.append((tick, track_idx, msg_idx, msg))
    events.sort(key=lambda event: event[:3])
    active: dict[tuple[int, int], tuple[int, Fraction, int, int]] = {}
    notes: list[dict[str, int]] = []
    previous_tick = 0
    elapsed = Fraction(0)
    tempo = 500_000
    tempo_at_tick: int | None = None
    tempo_count = 0
    for tick, track_idx, _msg_idx, msg in events:
        elapsed += Fraction((tick - previous_tick) * tempo, ticks_per_beat)
        previous_tick = tick
        if elapsed > MAX_DURATION_US:
            raise MidiScoreError("score runtime budget exceeded")
        kind = getattr(msg, "type", None)
        if kind == "set_tempo":
            next_tempo = _integer(getattr(msg, "tempo", None), 1, 16_777_215, "tempo")
            if tempo_at_tick == tick:
                if next_tempo != tempo:
                    raise MidiScoreError("conflicting same-tick tempo map")
                continue
            tempo = next_tempo
            tempo_at_tick = tick
            tempo_count += 1
            if tempo_count > MAX_TEMPOS:
                raise MidiScoreError("tempo change budget exceeded")
            continue
        if kind in ("sysex", "control_change", "pitchwheel"):
            raise MidiScoreError("unsupported MIDI controller/sysex semantics")
        if kind not in ("note_on", "note_off"):
            # Lyrics, filenames, vendor metadata are never exported.
            continue
        channel = _integer(getattr(msg, "channel", None), 0, 15, "channel")
        pitch = _integer(getattr(msg, "note", None), 0, 127, "pitch")
        velocity = _integer(getattr(msg, "velocity", None), 0, 127, "velocity")
        if channel == 9:
            raise MidiScoreError("percussion channel not a pitched piano score")
        key = (channel, pitch)  # MIDI channels are global across SMF tracks
        if kind == "note_on" and velocity > 0:
            if key in active:
                raise MidiScoreError("overlapping same-pitch note is ambiguous")
            if len(active) + len(notes) >= MAX_NOTES:
                raise MidiScoreError("note budget exceeded")
            active[key] = (tick, elapsed, velocity, track_idx)
        else:
            if key not in active:
                raise MidiScoreError("note_off has no matching note_on")
            started_tick, started_at, started_velocity, origin_track = active.pop(key)
            if tick <= started_tick:
                raise MidiScoreError("zero-duration note")
            start = _time_us(started_at)
            end = _time_us(elapsed)
            if end <= start:
                raise MidiScoreError("sub-microsecond note duration")
            notes.append({
                "track": origin_track, "channel": channel, "pitch": pitch,
                "velocity": started_velocity, "start_us": start,
                "end_us": end, "duration_us": end - start,
            })
    if active:
        raise MidiScoreError("unclosed note_on")
    notes.sort(key=lambda n: (
        n["start_us"], n["track"], n["channel"], n["pitch"], n["end_us"]
    ))
    return {
        "schema": SCHEMA,
        "visibility": source_kind,
        "license_id": license_id,
        "source_sha256": source_sha256,
        "backend": f"mido/{MIDO_VERSION}",
        "ticks_per_beat": ticks_per_beat,
        "track_count": len(tracks),
        "message_count": messages,
        "tempo_change_count": tempo_count,
        "note_count": len(notes),
        "duration_us": _time_us(elapsed),
        "notes": notes,
        "played": False,
        "physical_contact": False,
        "learner_state_modified": False,
    }


def read_midi_score(
    blob: bytes, *, enabled: bool = False, source_kind: str,
    expected_sha256: str, license_id: str | None = None,
) -> dict[str, Any]:
    """Explicit opt-in Mido 1.3.3 file parser from caller-supplied bytes."""
    if enabled is not True:
        raise MidiScoreError("MIDI import not explicitly enabled")
    _source(expected_sha256, source_kind, license_id)
    tracks, division = _preflight(blob)
    if hashlib.sha256(blob).hexdigest() != expected_sha256:
        raise MidiScoreError("MIDI source hash mismatch")
    try:
        installed = version("mido")
    except PackageNotFoundError as exc:
        raise MidiScoreError("optional mido==1.3.3 not installed") from exc
    if installed != MIDO_VERSION:
        raise MidiScoreError("unsupported optional Mido version")
    try:
        mido = import_module("mido")
        midi = mido.MidiFile(file=BytesIO(blob), clip=False)
    except (OSError, ValueError, EOFError, TypeError, ImportError,
            IndexError, KeyError, AssertionError) as exc:
        raise MidiScoreError("invalid MIDI content") from exc
    if midi.type not in (0, 1) or len(midi.tracks) != tracks or midi.ticks_per_beat != division:
        raise MidiScoreError("MIDI parser/header disagreement")
    return normalize_tracks(
        midi.tracks, ticks_per_beat=division,
        source_sha256=expected_sha256, source_kind=source_kind,
        license_id=license_id,
    )
