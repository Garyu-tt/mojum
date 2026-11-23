# Mido Standard MIDI File score reader (opt-in R0.1)

Implementation for [issue #758](https://github.com/Garyu-tt/mojum/issues/758). This is a **read-only public MIDI score fixture**, not a trained music-school brain, RoboPianist key activation adapter, autonomous action, music playback engine, live MIDI port or Noosphere event writer. The existing deterministic CPU-first Aquarium remains unchanged.

## Upstream and reproducibility

- Python 3.11+ core has **zero new dependencies**. The module only checks/imports Mido when explicitly invoked with `enabled=True`.
- Pin: **Mido 1.3.3**, upstream MIT license: <https://pypi.org/project/mido/1.3.3/>. API: <https://mido.readthedocs.io/en/stable/files/midi.html>. License to the software is **not** license to a musical composition or any third-party MIDI asset.
- Optional test environment: `python -m pip install 'mido==1.3.3'`.
- Stdlib/no-deps: `PYTHONPATH=src python -m unittest discover -s tests -p test_mido_score.py -v`; the native parser case skips when Mido is absent. A separate optional workflow runs the native case with pinned Mido on Python 3.11 and 3.13.
- All fixtures are synthetic and self-generated, with no model/data downloads.

## Explicit API

```python
from hashlib import sha256
from mojum.mido_score import read_midi_score

# midi_blob must come from a caller-approved, non-private licensed/public source.
score = read_midi_score(
    midi_blob,
    enabled=True,
    source_kind="PUBLIC_SYNTHETIC",  # or PUBLIC_LICENSED with license_id="CC0-1.0"
    expected_sha256=sha256(midi_blob).hexdigest(),
)
assert score["schema"] == "mojum.music-midi-score/1"
assert not score["played"]
assert not score["learner_state_modified"]
```

Supports Standard MIDI File type 0/1 with global tempo changes and deterministic microsecond note timing. Numeric projection includes original note pitch/velocity, track/channel and onset/offset times, SHA-256 of the exact file and pinned parser version. Timing accumulates in rational microseconds before deterministic round-half-up. Does **not** export track labels, lyrics, MIDI text, source filenames, personal identifiers or vendor metadata.

## Safety and resource budget

Preflight checks exact MIDI header and chunk framing before invoking Mido: max **64 KiB** input, **16 tracks**, **4096 messages**, **1024 notes**, **128 tempo changes**, **5,000,000 ticks**, **9600 PPQN**, and **10-minute** decoded timeline. Rejects SMPTE/type-2, unknown/trailing chunks, wrong hash, wrong package version, malformed timing, percussion channel, unsupported controller/sustain/bend/sysex semantics, overlapping same-channel/same-pitch notes across all tracks and unmatched note events. MIDI channels are global for type-1 files; note-on track provenance is preserved when a note-off appears in another track. PRIVATE/unknown source scopes and missing public license evidence fail closed.

Caller must independently verify license/source assertions; SPDX-shaped text is only a bounded claim, not license authentication. Parsing is within the caller's Python process, not a hardened OS sandbox. For hostile Internet MIDI, run an isolated parser worker before trusting it. This module never writes SQLite, Noosphere, Graph IR, music-school curriculum or real motor/device state.

## Completion and acceptance boundary

R0.1 provides the native Mido byte-parser adapter, strict offline normalization, negative tests, reference usage and optional CI. This does **not** claim native composition provenance, learning outcome, musical skill, motor control, general-purpose MIDI semantics or adversarial parser sandboxing. Keep Draft PR unmerged pending exact-head CI and independent QA.
