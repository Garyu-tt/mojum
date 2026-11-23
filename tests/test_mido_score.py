"""No-deps deterministic MIDI projection QA; optional native Mido 1.3.3 path."""
from __future__ import annotations

from io import BytesIO
import hashlib
from importlib.util import find_spec
import json
from types import SimpleNamespace
import struct
import unittest
from unittest.mock import patch

from mojum.mido_score import (
    MAX_MESSAGES, MidiScoreError, _preflight, normalize_tracks,
    read_midi_score,
)

HASH = "0" * 64


def event(kind: str, dt: int, **values):
    return SimpleNamespace(type=kind, time=dt, **values)


def notes_example():
    return [
        [event("set_tempo", 0, tempo=500000),
         event("note_on", 0, channel=0, note=60, velocity=85),
         event("set_tempo", 480, tempo=1000000),
         event("note_off", 480, channel=0, note=60, velocity=0)],
        [event("note_on", 240, channel=0, note=64, velocity=60),
         event("note_off", 480, channel=0, note=64, velocity=0)],
    ]


def score(tracks=None, **kw):
    args = dict(ticks_per_beat=480, source_sha256=HASH,
                source_kind="PUBLIC_SYNTHETIC")
    args.update(kw)
    return normalize_tracks(notes_example() if tracks is None else tracks, **args)


def midi_chunk(messages: bytes, *, fmt: int = 0, tracks: int = 1, division: int = 480):
    return b"MThd\0\0\0\x06" + struct.pack(">HHH", fmt, tracks, division) + (
        b"MTrk" + len(messages).to_bytes(4, "big") + messages
    )


class OfflineScoreTests(unittest.TestCase):
    def test_deterministic_tempo_merging_and_metadata_absent(self):
        a, b = score(), score()
        self.assertEqual(a, b)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(a["note_count"], 2)
        self.assertEqual(a["tempo_change_count"], 2)
        self.assertEqual(a["notes"][0]["start_us"], 0)
        self.assertEqual(a["notes"][0]["end_us"], 1_500_000)
        self.assertEqual(a["notes"][1]["start_us"], 250_000)
        self.assertEqual(a["notes"][1]["end_us"], 1_000_000)
        self.assertFalse(a["played"])
        self.assertFalse(a["learner_state_modified"])
        self.assertNotIn("midi_meta_text", json.dumps(a))

    def test_exact_submicrosecond_rational_timing(self):
        a = score([[event("note_on", 0, channel=0, note=1, velocity=1),
                    event("note_off", 3, channel=0, note=1, velocity=0)]],
                  ticks_per_beat=3)
        self.assertEqual(a["notes"][0]["end_us"], 500000)

    def test_reject_private_unlicensed_wrong_hash(self):
        for source_kind, license_id in [
            ("PRIVATE", None), ("PRIVATE", "CC0-1.0"),
            ("PUBLIC_LICENSED", None), ("PUBLIC_SYNTHETIC", "CC0-1.0"),
            ("PUBLIC_LICENSED", "CC0-1.0\nTOKEN"),
        ]:
            with self.subTest(source_kind=source_kind), self.assertRaises(MidiScoreError):
                score(source_kind=source_kind, license_id=license_id)
        with self.assertRaises(MidiScoreError):
            score(source_sha256="z" * 64)

    def test_public_source_kind_cannot_be_comparison_spoofed(self):
        # An int subclass can compare equal to a string but serialize as a
        # JSON number, corrupting the visibility discriminator.
        class PretendPublic(int):
            def __eq__(self, other):
                return other in ("PUBLIC_SYNTHETIC", "PUBLIC_LICENSED")

        for source_kind in (PretendPublic(7), b"PUBLIC_SYNTHETIC"):
            with self.subTest(source_kind=type(source_kind).__name__):
                with self.assertRaisesRegex(MidiScoreError, "source kind"):
                    score(source_kind=source_kind)

    def test_licensed_public_no_private_payload(self):
        a = score(source_kind="PUBLIC_LICENSED", license_id="CC0-1.0")
        self.assertEqual(a["license_id"], "CC0-1.0")
        self.assertNotIn("agent", str(a))

    def test_reject_bool_negative_and_oversize_timing(self):
        for delta in (True, -1, 5_000_001, 0.5):
            with self.subTest(delta=delta), self.assertRaises(MidiScoreError):
                score([[event("end_of_track", delta)]])
        with self.assertRaises(MidiScoreError):
            score(ticks_per_beat=True)
        with self.assertRaises(MidiScoreError):
            score([[event("set_tempo", 0, tempo=16_777_215),
                    event("end_of_track", 480_000)]])

    def test_reject_ambiguous_notes_and_unsupported_semantics(self):
        cases = [
            [event("note_off", 0, channel=0, note=60, velocity=0)],
            [event("note_on", 0, channel=0, note=60, velocity=50)],
            [event("note_on", 0, channel=0, note=60, velocity=50),
             event("note_on", 1, channel=0, note=60, velocity=30)],
            [event("note_on", 0, channel=0, note=60, velocity=50),
             event("note_off", 0, channel=0, note=60, velocity=0)],
            [event("note_on", 0, channel=9, note=36, velocity=50)],
            [event("control_change", 0, control=64, value=127)],
            [event("pitchwheel", 0, pitch=123)],
            [event("sysex", 0, data=bytes((1, 2)))],
            [event("note_on", 0, channel=0, note=60, velocity=True)],
        ]
        for events in cases:
            with self.subTest(events=str(events)), self.assertRaises(MidiScoreError):
                score([events])

    def test_channel_key_global_across_type1_tracks(self):
        # MIDI channels are shared by all tracks in a format-1 file.
        # The same channel/pitch cannot be independently active twice.
        duplicate = [
            [event("note_on", 0, channel=0, note=60, velocity=80),
             event("note_off", 480, channel=0, note=60, velocity=0)],
            [event("note_on", 240, channel=0, note=60, velocity=70),
             event("note_off", 480, channel=0, note=60, velocity=0)],
        ]
        with self.assertRaisesRegex(MidiScoreError, "overlapping same-pitch"):
            score(duplicate)

        # A note-off on a different track closes the channel note, but
        # provenance still identifies the track of its note-on.
        cross_track_off = [
            [event("note_on", 0, channel=0, note=60, velocity=80)],
            [event("note_off", 480, channel=0, note=60, velocity=0)],
        ]
        result = score(cross_track_off)
        self.assertEqual(result["note_count"], 1)
        self.assertEqual(result["notes"][0]["track"], 0)
        self.assertEqual(result["notes"][0]["duration_us"], 500_000)

    def test_reject_simultaneous_conflicting_tempo_and_budgets(self):
        with self.assertRaises(MidiScoreError):
            score([[event("set_tempo", 0, tempo=500000)],
                   [event("set_tempo", 0, tempo=600000)]])
        with self.assertRaises(MidiScoreError):
            score([[event("end_of_track", 0)] * (MAX_MESSAGES + 1)])
        with self.assertRaises(MidiScoreError):
            score([[event("end_of_track", 0)]] * 17)

    def test_no_private_metadata_projection(self):
        a = score([[event("track_name", 0, name="PRIVATE_KEY_DO_NOT_ECHO"),
                    event("lyrics", 0, text="PRIVATE_LYRICS_DO_NOT_ECHO"),
                    event("note_on", 0, channel=0, note=60, velocity=80),
                    event("note_off", 480, channel=0, note=60, velocity=0)]])
        self.assertNotIn("PRIVATE", json.dumps(a))

    def test_preflight_reject_malformed_and_extra_chunks(self):
        valid = midi_chunk(b"\x00\xff\x2f\x00")
        self.assertEqual(_preflight(valid), (1, 480))
        invalid = [
            valid + b"EXTRA", valid[:-1],
            valid[:4] + b"\0\0\0\x07" + valid[8:],
            valid[:12] + b"\xe7\x28" + valid[14:],
            midi_chunk(b"\x00\xff\x2f\x00", division=0),
            midi_chunk(b"\x00\xff\x2f\x00", fmt=2),
            midi_chunk(b"\x00\xff\x2f\x00", fmt=0, tracks=2),
            valid + b"a" * 70_000, bytearray(valid), b"", None,
        ]
        for blob in invalid:
            with self.subTest(head=str(blob)[:30]), self.assertRaises(MidiScoreError):
                _preflight(blob)

    def test_opt_in_refusal_before_import(self):
        blob = midi_chunk(b"\x00\xff\x2f\x00")
        sha = hashlib.sha256(blob).hexdigest()
        with patch("mojum.mido_score.import_module", side_effect=AssertionError("imported")):
            with self.assertRaisesRegex(MidiScoreError, "not explicitly enabled"):
                read_midi_score(blob, source_kind="PUBLIC_SYNTHETIC", expected_sha256=sha)

    def test_exact_hash_before_mido_import(self):
        blob = midi_chunk(b"\x00\xff\x2f\x00")
        with patch("mojum.mido_score.import_module", side_effect=AssertionError("imported")):
            with self.assertRaisesRegex(MidiScoreError, "source hash mismatch"):
                read_midi_score(blob, enabled=True, source_kind="PUBLIC_SYNTHETIC",
                                expected_sha256=HASH)

    def test_version_fails_closed(self):
        blob = midi_chunk(b"\x00\xff\x2f\x00")
        sha = hashlib.sha256(blob).hexdigest()
        with patch("mojum.mido_score.version", return_value="1.3.2"):
            with self.assertRaisesRegex(MidiScoreError, "unsupported optional Mido version"):
                read_midi_score(blob, enabled=True, source_kind="PUBLIC_SYNTHETIC",
                                expected_sha256=sha)

    @unittest.skipUnless(find_spec("mido") is not None, "mido optional package absent")
    def test_actual_mido_133_bytes_parser(self):
        from importlib.metadata import version
        if version("mido") != "1.3.3":
            self.skipTest("other native Mido version installed")
        import mido
        track0 = mido.MidiTrack([
            mido.MetaMessage("set_tempo", tempo=600_000, time=0),
            mido.Message("note_on", channel=0, note=60, velocity=100, time=0),
            mido.Message("note_off", channel=0, note=60, velocity=0, time=480),
            mido.MetaMessage("end_of_track", time=0),
        ])
        mid = mido.MidiFile(type=0, ticks_per_beat=480, tracks=[track0])
        stream = BytesIO()
        mid.save(file=stream)
        blob = stream.getvalue()
        sha = hashlib.sha256(blob).hexdigest()
        a = read_midi_score(blob, enabled=True, source_kind="PUBLIC_SYNTHETIC",
                            expected_sha256=sha)
        b = read_midi_score(blob, enabled=True, source_kind="PUBLIC_SYNTHETIC",
                            expected_sha256=sha)
        self.assertEqual(a, b)
        self.assertEqual(a["notes"][0]["duration_us"], 600_000)
        self.assertEqual(a["backend"], "mido/1.3.3")


if __name__ == "__main__":
    unittest.main()
