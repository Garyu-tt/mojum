import hashlib
import json
import unittest
from dataclasses import FrozenInstanceError

from mojum.segment_manifest import (
    EMPTY_HEAD,
    MAX_MANIFEST_BYTES,
    SCHEMA,
    SegmentManifest,
    SegmentManifestError,
    create_manifest,
    parse_manifest,
    validate_manifest,
    verify_segment_bytes,
)


class SegmentManifestTests(unittest.TestCase):
    def setUp(self):
        self.segment = b"PUBLIC_SYNTHETIC segment payload"
        self.content_hash = hashlib.sha256(self.segment).hexdigest()

    def make(self, **overrides):
        values = {
            "agent_id": "fixture-organism",
            "first_seq": 1,
            "last_seq": 4,
            "previous_segment_head": EMPTY_HEAD,
            "source_schema": "mojum.noosphere.event/1",
            "content_sha256": self.content_hash,
            "origin_device": "fixture-node-a",
            "created_at_ns": 123456789,
        }
        values.update(overrides)
        return create_manifest(**values)

    def test_genesis_manifest_round_trips_canonically(self):
        manifest = self.make()
        parsed = parse_manifest(manifest.canonical_bytes())
        self.assertEqual(parsed, manifest)
        self.assertEqual(manifest.event_count, 4)
        self.assertEqual(len(manifest.manifest_sha256), 64)

    def test_linked_segment_requires_previous_head(self):
        first = self.make()
        second = self.make(
            first_seq=5, last_seq=7,
            previous_segment_head=first.segment_head,
        )
        self.assertEqual(second.event_count, 3)
        self.assertNotEqual(second.segment_head, first.segment_head)

    def test_genesis_cannot_claim_nonempty_previous_head(self):
        with self.assertRaises(SegmentManifestError):
            self.make(previous_segment_head="a" * 64)

    def test_non_genesis_cannot_claim_empty_previous_head(self):
        with self.assertRaises(SegmentManifestError):
            self.make(first_seq=2, last_seq=4)

    def test_sequence_count_must_match_inclusive_range(self):
        data = self.make().to_dict()
        data["event_count"] = 3
        with self.assertRaises(SegmentManifestError):
            validate_manifest(data)

    def test_bool_is_not_an_integer(self):
        data = self.make().to_dict()
        data["created_at_ns"] = True
        with self.assertRaises(SegmentManifestError):
            validate_manifest(data)

    def test_digest_requires_lowercase_hex(self):
        data = self.make().to_dict()
        data["content_sha256"] = "A" * 64
        with self.assertRaises(SegmentManifestError):
            validate_manifest(data)

    def test_unknown_and_missing_fields_are_rejected(self):
        data = self.make().to_dict()
        data["extra"] = "ignored?"
        with self.assertRaises(SegmentManifestError):
            validate_manifest(data)
        data.pop("extra")
        data.pop("origin_device")
        with self.assertRaises(SegmentManifestError):
            validate_manifest(data)

    def test_head_tampering_is_rejected(self):
        data = self.make().to_dict()
        data["last_seq"] = 5
        data["event_count"] = 5
        with self.assertRaises(SegmentManifestError):
            validate_manifest(data)

    def test_duplicate_json_keys_are_rejected(self):
        raw = self.make().canonical_bytes()
        text = raw.decode("utf-8")
        duplicate = text[:-1] + ',"agent_id":"shadow"}'
        with self.assertRaises(SegmentManifestError):
            parse_manifest(duplicate.encode("utf-8"))

    def test_noncanonical_json_is_rejected(self):
        data = self.make().to_dict()
        raw = json.dumps(data, indent=2).encode("utf-8")
        with self.assertRaises(SegmentManifestError):
            parse_manifest(raw)

    def test_oversized_wire_is_rejected(self):
        with self.assertRaises(SegmentManifestError):
            parse_manifest(b" " * (MAX_MANIFEST_BYTES + 1))

    def test_mutation_of_returned_dict_does_not_change_manifest(self):
        manifest = self.make()
        detached = manifest.to_dict()
        detached["agent_id"] = "changed"
        self.assertEqual(manifest.agent_id, "fixture-organism")

    def test_dataclass_is_immutable(self):
        manifest = self.make()
        with self.assertRaises(FrozenInstanceError):
            manifest.agent_id = "changed"

    def test_content_bytes_are_verified_separately(self):
        manifest = self.make()
        self.assertTrue(verify_segment_bytes(manifest, self.segment))
        self.assertFalse(verify_segment_bytes(manifest, b"tampered"))

    def test_exact_bytes_required_for_content_verification(self):
        with self.assertRaises(SegmentManifestError):
            verify_segment_bytes(self.make(), bytearray(self.segment))

    def test_directly_constructed_invalid_manifest_is_revalidated(self):
        valid = self.make()
        forged = SegmentManifest(
            schema=valid.schema, agent_id=valid.agent_id,
            first_seq=valid.first_seq, last_seq=valid.last_seq,
            event_count=valid.event_count,
            previous_segment_head=valid.previous_segment_head,
            segment_head="f" * 64, source_schema=valid.source_schema,
            content_sha256=valid.content_sha256,
            origin_device=valid.origin_device, created_at_ns=valid.created_at_ns,
        )
        with self.assertRaises(SegmentManifestError):
            verify_segment_bytes(forged, self.segment)

    def test_wrong_schema_is_rejected(self):
        data = self.make().to_dict()
        data["schema"] = "mojum.noosphere.segment-manifest/999"
        with self.assertRaises(SegmentManifestError):
            validate_manifest(data)

    def test_bad_text_and_surrogates_are_rejected(self):
        with self.assertRaises(SegmentManifestError):
            self.make(agent_id="\ud800")
        with self.assertRaises(SegmentManifestError):
            self.make(origin_device="  ")

    def test_exact_dict_only(self):
        class DictSubclass(dict):
            pass
        with self.assertRaises(SegmentManifestError):
            validate_manifest(DictSubclass(self.make().to_dict()))


if __name__ == "__main__":
    unittest.main()
