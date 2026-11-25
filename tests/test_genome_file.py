"""Strict GENESIS file admission before any persistent runtime is opened."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.genesis import GenesisError, validate_seed
from mojum.genome_file import GenomeFileError, MAX_GENOME_BYTES, load_seed_file


SEED = {
    "schema": "mojum.genesis.genotype/1",
    "genome_id": "genome-file-synthetic-星🧬",
    "revision": "fixture-r1",
    "inherited": {
        "heartbeat_bpm": 60,
        "body_scale_milli": 1000,
        "sensory_gain_milli": 1000,
    },
}


class GenomeFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "genome.json"
        self.raw = json.dumps(SEED, ensure_ascii=False).encode("utf-8")
        self.path.write_bytes(self.raw)

    def assert_closed(self, descriptor):
        try:
            os.fstat(descriptor)
        except OSError:
            return
        os.close(descriptor)
        self.fail("genome reader leaked its owned file descriptor")

    def test_valid_seed_is_canonical_validated_and_detached(self):
        for path in (self.path, str(self.path), os.fsencode(self.path)):
            with self.subTest(path_type=type(path).__name__):
                actual = load_seed_file(path)
                self.assertEqual(actual, validate_seed(SEED))
                actual["inherited"]["heartbeat_bpm"] = 180
                self.assertEqual(load_seed_file(path), SEED)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_regular_file_symlink_keeps_existing_input_compatibility(self):
        linked = self.directory / "linked-genome.json"
        try:
            linked.symlink_to(self.path)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"file symlinks unavailable: {exc}")
        self.assertEqual(load_seed_file(linked), SEED)
        self.assertTrue(linked.is_symlink())

    def test_exact_byte_limit_is_accepted_and_next_byte_is_rejected(self):
        self.assertLess(len(self.raw), MAX_GENOME_BYTES)
        at_limit = self.raw + b" " * (MAX_GENOME_BYTES - len(self.raw))
        self.path.write_bytes(at_limit)
        self.assertEqual(load_seed_file(self.path), SEED)

        oversized = at_limit + b" "
        # The non-ASCII ID makes the character count smaller than the byte cap.
        # A character-based rather than byte-based admission would accept it.
        self.assertLess(len(oversized.decode("utf-8")), MAX_GENOME_BYTES)
        self.path.write_bytes(oversized)
        with self.assertRaises(GenomeFileError):
            load_seed_file(self.path)

    def test_oversized_bytes_are_rejected_before_json_parsing(self):
        self.path.write_bytes(b"\xff" * (MAX_GENOME_BYTES + 1))
        with patch("mojum.genome_file.json.loads") as parse:
            with self.assertRaises(GenomeFileError):
                load_seed_file(self.path)
        parse.assert_not_called()

    def test_malformed_utf8_and_json_are_normalized(self):
        cases = {
            "empty": b"",
            "unfinished object": b"{",
            "trailing document": self.raw + b"{}",
            "invalid UTF-8": b"\xff",
            "UTF-8 BOM": b"\xef\xbb\xbf" + self.raw,
            "recursive array": b"[" * 10_000 + b"]" * 10_000,
        }
        for description, raw in cases.items():
            with self.subTest(description=description):
                self.path.write_bytes(raw)
                with self.assertRaises(GenomeFileError):
                    load_seed_file(self.path)

    def test_duplicate_keys_are_rejected_at_every_object_depth(self):
        text = self.raw.decode("utf-8")
        cases = {
            "different identity": text.replace(
                '"genome_id":', '"genome_id":"first-identity","genome_id":', 1),
            "same identity": text.replace(
                '"genome_id":',
                '"genome_id":"genome-file-synthetic-星🧬","genome_id":', 1),
            "escaped identity key": text.replace(
                '"genome_id":', '"genome\\u005fid":"escaped-first","genome_id":', 1),
            "inherited gene": text.replace(
                '"heartbeat_bpm":', '"heartbeat_bpm":90,"heartbeat_bpm":', 1),
            "inherited object": text.replace(
                '"inherited":', '"inherited":{},"inherited":', 1),
        }
        for description, value in cases.items():
            with self.subTest(description=description):
                self.path.write_text(value, encoding="utf-8")
                with self.assertRaises(GenomeFileError):
                    load_seed_file(self.path)

    def test_nonfinite_json_tokens_are_rejected_before_schema_validation(self):
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token):
                self.path.write_text('{"ignored":' + token + '}', encoding="utf-8")
                with self.assertRaises(GenomeFileError):
                    load_seed_file(self.path)

    def test_schema_and_gene_rules_still_use_genesis_errors(self):
        invalid = [
            {},
            {**SEED, "schema": "mojum.genesis.genotype/999"},
            {**SEED, "unexpected": 1},
            {**SEED, "genome_id": " "},
            {**SEED, "revision": "x" * 129},
            {**SEED, "inherited": {**SEED["inherited"], "heartbeat_bpm": True}},
            {**SEED, "inherited": {**SEED["inherited"], "heartbeat_bpm": 181}},
        ]
        for seed in invalid:
            with self.subTest(seed=seed):
                self.path.write_text(json.dumps(seed), encoding="utf-8")
                with self.assertRaises(GenesisError):
                    load_seed_file(self.path)

    def test_lone_surrogates_remain_genesis_schema_errors(self):
        for field in ("genome_id", "revision"):
            with self.subTest(field=field):
                seed = {**SEED, field: "\ud800"}
                self.path.write_text(json.dumps(seed), encoding="utf-8")
                with self.assertRaises(GenesisError):
                    load_seed_file(self.path)

    def test_unreadable_and_nonregular_paths_are_normalized(self):
        for path in (self.directory / "missing.json", self.directory, "invalid\0path"):
            with self.subTest(path=path):
                with self.assertRaises(GenomeFileError):
                    load_seed_file(path)

    def test_caller_file_descriptor_is_not_consumed_or_closed(self):
        with self.path.open("rb") as caller:
            first = caller.read(1)
            position = caller.tell()
            with patch("mojum.genome_file.os.open") as open_file:
                with self.assertRaises(GenomeFileError):
                    load_seed_file(caller.fileno())
            open_file.assert_not_called()
            self.assertEqual(caller.tell(), position)
            self.assertFalse(caller.closed)
            self.assertEqual(first + caller.read(), self.raw)

        for path in (True, False, None, object()):
            with self.subTest(path_type=type(path).__name__):
                with patch("mojum.genome_file.os.open") as open_file:
                    with self.assertRaises(GenomeFileError):
                        load_seed_file(path)
                open_file.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX nonblocking admission")
    def test_missing_nonblock_support_fails_before_open(self):
        with patch("mojum.genome_file.os.O_NONBLOCK", 0, create=True), \
             patch("mojum.genome_file.os.open") as open_file:
            with self.assertRaises(GenomeFileError):
                load_seed_file(self.path)
        open_file.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX character device fixture")
    def test_actual_opened_descriptor_must_be_regular(self):
        # The pathname names a valid seed, but the descriptor is a device.
        # Checking only path.stat() would admit the replacement descriptor.
        real_open = os.open
        descriptors = []

        def substitute(path, flags):
            descriptor = real_open(os.devnull, flags)
            descriptors.append(descriptor)
            return descriptor

        with patch("mojum.genome_file.os.open", side_effect=substitute):
            with self.assertRaises(GenomeFileError):
                load_seed_file(self.path)
        self.assertEqual(len(descriptors), 1)
        self.assert_closed(descriptors[0])

    def test_owned_descriptor_closes_when_fstat_or_fdopen_fails(self):
        real_open = os.open
        for operation, error_type in (("os.fstat", OSError),
                                      ("os.fdopen", OSError),
                                      ("os.fdopen", ValueError)):
            with self.subTest(operation=operation, error_type=error_type.__name__):
                descriptors = []

                def record_open(path, flags):
                    descriptor = real_open(path, flags)
                    descriptors.append(descriptor)
                    return descriptor

                with patch("mojum.genome_file.os.open", side_effect=record_open), \
                     patch("mojum.genome_file." + operation,
                           side_effect=error_type("injected failure")):
                    with self.assertRaises(GenomeFileError):
                        load_seed_file(self.path)
                self.assertEqual(len(descriptors), 1)
                self.assert_closed(descriptors[0])

    def test_owned_descriptor_closes_when_read_fails(self):
        real_open = os.open
        real_fdopen = os.fdopen
        descriptors = []

        def record_open(path, flags):
            descriptor = real_open(path, flags)
            descriptors.append(descriptor)
            return descriptor

        class Unreadable:
            def __init__(self, descriptor, mode):
                self.stream = real_fdopen(descriptor, mode)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.stream.close()

            def read(self, size):
                raise OSError("injected read failure")

        with patch("mojum.genome_file.os.open", side_effect=record_open), \
             patch("mojum.genome_file.os.fdopen", side_effect=Unreadable):
            with self.assertRaises(GenomeFileError):
                load_seed_file(self.path)
        self.assertEqual(len(descriptors), 1)
        self.assert_closed(descriptors[0])


if __name__ == "__main__":
    unittest.main()
