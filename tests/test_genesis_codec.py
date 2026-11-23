import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from mojum.genesis import ACQUISITION_SCHEMA, GenomeLedger
from mojum.genesis_codec import MAX_SNAPSHOT_BYTES, GenesisCodecError, dumps, loads


SEED = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "genesis_seed.json").read_text()
)


def make_event(event_id="event-1", recorded=200, tick=10):
    return {
        "schema": ACQUISITION_SCHEMA,
        "event_id": event_id,
        "genome_id": SEED["genome_id"],
        "gene": "heartbeat_bpm",
        "delta": 1,
        "valid_from_tick": tick,
        "observed_at_ns": recorded - 1,
        "recorded_at_ns": recorded,
        "source": "test:fixture",
        "evidence": "PUBLIC_SYNTHETIC codec fixture only",
    }


class GenesisCodecTests(unittest.TestCase):
    def ledger(self):
        ledger = GenomeLedger(SEED)
        ledger.append(make_event(), ledger.revision)
        ledger.append(make_event("event-2", recorded=300, tick=20), ledger.revision)
        return ledger

    def test_round_trip_preserves_revision_and_phenotype(self):
        original = self.ledger()
        restored = loads(dumps(original))
        self.assertEqual(restored.revision, original.revision)
        for tick in (0, 10, 19, 20, 99):
            self.assertEqual(restored.phenotype(tick), original.phenotype(tick))

    def test_canonical_encoding_is_byte_for_byte_deterministic(self):
        first = self.ledger()
        second = GenomeLedger.from_snapshot(first.snapshot())
        self.assertEqual(dumps(first), dumps(second))
        self.assertEqual(dumps(first), dumps(first))

    def test_accepts_str_input_and_unicode_metadata(self):
        encoded = dumps(self.ledger()).decode("utf-8")
        self.assertEqual(loads(encoded).revision, self.ledger().revision)

    def test_duplicate_root_key_rejected(self):
        raw = b'{"schema":"a","schema":"b","seed":{},"events":[],"revision":"x"}'
        with self.assertRaisesRegex(GenesisCodecError, "Duplicate"):
            loads(raw)

    def test_duplicate_nested_key_rejected(self):
        raw = (
            b'{"schema":"mojum.genesis.ledger/1",'
            b'"seed":{"schema":"x","schema":"y"},'
            b'"events":[],"revision":"x"}'
        )
        with self.assertRaisesRegex(GenesisCodecError, "Duplicate"):
            loads(raw)

    def test_non_finite_numbers_rejected(self):
        for value in (b"NaN", b"Infinity", b"-Infinity"):
            raw = b'{"schema":"x","seed":{},"events":[],"revision":' + value + b"}"
            with self.subTest(value=value):
                with self.assertRaises(GenesisCodecError):
                    loads(raw)

    def test_invalid_utf8_rejected(self):
        with self.assertRaisesRegex(GenesisCodecError, "strict UTF-8"):
            loads(b'{"bad":"\xff"}')

    def test_bom_rejected(self):
        with self.assertRaisesRegex(GenesisCodecError, "BOM"):
            loads(b"\xef\xbb\xbf{}")

    def test_empty_and_wrong_type_rejected(self):
        for value in (b"", "", None, 1, bytearray(b"{}")):
            with self.subTest(value=value):
                with self.assertRaises(GenesisCodecError):
                    loads(value)

    def test_oversized_input_rejected_before_parse(self):
        with patch("mojum.genesis_codec.MAX_SNAPSHOT_BYTES", 8):
            with self.assertRaisesRegex(GenesisCodecError, "byte limit"):
                loads(b" " * 9)

    def test_oversized_text_rejected_before_encoding(self):
        payload = " " * (MAX_SNAPSHOT_BYTES + 1)
        with patch(
            "mojum.genesis_codec._encode_text",
            side_effect=AssertionError("oversized text must be refused before encoding"),
        ):
            with self.assertRaisesRegex(GenesisCodecError, "byte limit"):
                loads(payload)

    def test_oversized_output_rejected(self):
        with patch("mojum.genesis_codec.MAX_SNAPSHOT_BYTES", 8):
            with self.assertRaisesRegex(GenesisCodecError, "byte limit"):
                dumps(GenomeLedger(SEED))

    def test_non_object_root_rejected(self):
        for raw in (b"[]", b"null", b'"text"', b"7"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(GenesisCodecError, "root"):
                    loads(raw)

    def test_corrupt_revision_rejected(self):
        snapshot = self.ledger().snapshot()
        snapshot["revision"] = "0" * 64
        with self.assertRaisesRegex(GenesisCodecError, "validation"):
            loads(json.dumps(snapshot).encode("utf-8"))

    def test_unknown_schema_rejected(self):
        snapshot = self.ledger().snapshot()
        snapshot["schema"] = "mojum.genesis.ledger/999"
        with self.assertRaises(GenesisCodecError):
            loads(json.dumps(snapshot))

    def test_invalid_event_rejected(self):
        snapshot = self.ledger().snapshot()
        snapshot["events"][0]["delta"] = True
        with self.assertRaises(GenesisCodecError):
            loads(json.dumps(snapshot))

    def test_dumps_requires_exact_ledger_instance(self):
        with self.assertRaises(GenesisCodecError):
            dumps(object())

    def test_snapshot_returned_by_restored_ledger_is_detached(self):
        restored = loads(dumps(self.ledger()))
        exposed = restored.snapshot()
        exposed["events"][0]["delta"] = 99
        self.assertEqual(restored.phenotype(10)["parameters"]["heartbeat_bpm"], 61)

    def test_round_trip_with_empty_ledger(self):
        ledger = GenomeLedger(copy.deepcopy(SEED))
        restored = loads(dumps(ledger))
        self.assertEqual(restored.snapshot(), ledger.snapshot())


if __name__ == "__main__":
    unittest.main()
