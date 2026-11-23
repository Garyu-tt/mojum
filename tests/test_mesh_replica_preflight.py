"""Hermetic synthetic tests for advisory Continuity Mesh replica preflight."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from mojum.mesh_replica_preflight import (
    ManifestError, MAX_MANIFEST_BYTES, SCHEMA,
    compare, main, parse_manifest, read_manifest,
)


def sample(**overrides):
    item = {
        "schema": SCHEMA,
        "organism_id": "dante.synthetic",
        "node_id": "tower-a",
        "epoch": 5,
        "sequence": 44,
        "journal_head_sha256": "a" * 64,
        "checkpoint_sha256": "b" * 64,
    }
    item.update(overrides)
    return item


def parse(**overrides):
    return parse_manifest(json.dumps(sample(**overrides)).encode())


class ManifestTests(unittest.TestCase):
    def test_valid_and_no_promote_even_with_equal_digest(self):
        d = compare(parse(), parse(node_id="tower-b"))
        self.assertEqual(d.status, "match")
        self.assertFalse(d.authorizes_promotion)
        self.assertTrue(d.requires_external_fencing)
        self.assertEqual(d.as_dict()["reason"], "metadata_equal_requires_independent_verification")

    def test_stale_has_no_ancestry_proof(self):
        d = compare(parse(), parse(node_id="tower-b", sequence=43))
        self.assertEqual((d.status, d.reason), ("stale", "replica_behind_unproven_ancestry"))

    def test_ahead_rejected(self):
        self.assertEqual(compare(parse(), parse(node_id="tower-b", sequence=45)).status, "diverged")

    def test_same_node_rejected(self):
        self.assertEqual(compare(parse(), parse()).reason, "not_independent_node_ids")

    def test_wrong_organism(self):
        self.assertEqual(compare(parse(), parse(node_id="tower-b", organism_id="other")).reason, "organism_mismatch")

    def test_split_epochs(self):
        self.assertEqual(compare(parse(), parse(node_id="tower-b", epoch=6)).reason, "epoch_mismatch_requires_fencing")

    def test_equal_seq_different_journal(self):
        self.assertEqual(compare(parse(), parse(node_id="tower-b", journal_head_sha256="0" * 64)).reason, "journal_head_mismatch")

    def test_equal_journal_different_checkpoint(self):
        self.assertEqual(compare(parse(), parse(node_id="tower-b", checkpoint_sha256="0" * 64)).reason, "checkpoint_digest_mismatch")

    def test_strict_fields(self):
        for change in ({"extra": 1}, {"schema": "v0"}, {"organism_id": "../tmp"}, {"node_id": ""}, {"checkpoint_sha256": "B" * 64}):
            with self.subTest(change=change), self.assertRaises(ManifestError):
                parse(**change)

    def test_bool_is_not_int(self):
        for field in ("epoch", "sequence"):
            with self.subTest(field=field), self.assertRaises(ManifestError):
                parse(**{field: True})

    def test_zero_negative_and_overflow(self):
        for n in (0, -1, 1 << 63):
            with self.subTest(n=n), self.assertRaises(ManifestError):
                parse(sequence=n)

    def test_duplicate_key_rejected(self):
        raw = json.dumps(sample()).replace('"epoch": 5', '"epoch": 4, "epoch": 5')
        with self.assertRaises(ManifestError):
            parse_manifest(raw.encode())

    def test_invalid_utf8_nested_json_nonfinite_and_trailing(self):
        for raw in (bytes((255,)), b"[]", b"{}{}", b'{"v":NaN}', b"[" * 1100 + b"]" * 1100):
            with self.subTest(data=raw[:20]), self.assertRaises(ManifestError):
                parse_manifest(raw)

    def test_reject_oversize_and_non_bytes(self):
        for raw in (b"", b" " * (MAX_MANIFEST_BYTES + 1), "text", bytearray(b"{}")):
            with self.subTest(type=type(raw)), self.assertRaises(ManifestError):
                parse_manifest(raw)

    def test_in_memory_forgery_not_trusted(self):
        from mojum.mesh_replica_preflight import ReplicaHead
        good = parse()
        bad = ReplicaHead(good.organism_id, "tower-b", True, 44, "a" * 64, "b" * 64)
        with self.assertRaises(ManifestError):
            compare(good, bad)

    def test_file_and_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first, second = root / "first.json", root / "second.json"
            first.write_text(json.dumps(sample()), encoding="utf-8")
            second.write_text(json.dumps(sample(node_id="tower-b")), encoding="utf-8")
            self.assertEqual(read_manifest(first).node_id, "tower-a")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--source", str(first), "--replica", str(second)])
            self.assertEqual(code, 0)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["status"], "match")
            self.assertFalse(result["authorizes_promotion"])
            second.write_text(json.dumps(sample(node_id="tower-b", sequence=10)), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--source", str(first), "--replica", str(second)]), 2)
            second.write_bytes(b"{}")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--source", str(first), "--replica", str(second)]), 1)

    def test_symlink_and_non_regular_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.json"
            source.write_text(json.dumps(sample()), encoding="utf-8")
            alias = root / "alias.json"
            try:
                alias.symlink_to(source)
            except (OSError, NotImplementedError):
                self.skipTest("no symlink permissions")
            with self.assertRaises(ManifestError):
                read_manifest(alias)
            with self.assertRaises(ManifestError):
                read_manifest(root)
            with self.assertRaises(ManifestError):
                read_manifest(root / "missing.json")


if __name__ == "__main__":
    unittest.main()
