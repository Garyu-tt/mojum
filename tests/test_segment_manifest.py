import hashlib
import json
import unittest

from mojum.segment_manifest import (
    SCHEMA, ZERO_HEAD, SegmentManifestError, build_manifest,
    parse_manifest_json, validate_manifest,
)


CONTENT = hashlib.sha256(b"synthetic-segment").hexdigest()


def sample():
    return build_manifest(
        agent_id="agent.demo",
        first_seq=10,
        last_seq=12,
        previous_segment_head=None,
        source_schema="mojum.events/1",
        content_sha256=CONTENT,
        origin_device_id="device.fixture-01",
        created_at_ns=123456789,
    ).as_dict()


class SegmentManifestTests(unittest.TestCase):
    def test_builds_consistent_inclusive_range_and_head(self):
        result = build_manifest(
            agent_id="agent.demo", first_seq=10, last_seq=12,
            previous_segment_head=None, source_schema="mojum.events/1",
            content_sha256=CONTENT, origin_device_id="device.fixture-01",
            created_at_ns=123456789,
        )
        self.assertEqual(result.event_count, 3)
        self.assertEqual(result.segment_head, sample()["segment_head"])

    def test_round_trip_wire_is_deterministic(self):
        data = sample()
        wire = json.dumps(data, sort_keys=True, separators=(",", ":"))
        parsed = parse_manifest_json(wire)
        self.assertEqual(parsed, validate_manifest(data))
        self.assertEqual(parsed.canonical_json(), wire)

    def test_previous_head_accepts_chain_link(self):
        data = build_manifest(
            agent_id="agent.demo", first_seq=13, last_seq=13,
            previous_segment_head=sample()["segment_head"],
            source_schema="mojum.events/1", content_sha256=CONTENT,
            origin_device_id="device.fixture-01", created_at_ns=123456790,
        )
        self.assertEqual(data.previous_segment_head, sample()["segment_head"])

    def test_rejects_missing_and_unknown_fields(self):
        data = sample()
        del data["origin_device_id"]
        with self.assertRaises(SegmentManifestError):
            validate_manifest(data)
        data = sample()
        data["extra"] = "ignored?"
        with self.assertRaises(SegmentManifestError):
            validate_manifest(data)

    def test_rejects_boolean_as_integer(self):
        data = sample()
        data["first_seq"] = True
        with self.assertRaises(SegmentManifestError):
            validate_manifest(data)

    def test_rejects_inconsistent_event_count(self):
        data = sample()
        data["event_count"] = 99
        with self.assertRaises(SegmentManifestError):
            validate_manifest(data)

    def test_rejects_invalid_sequence_interval(self):
        with self.assertRaises(SegmentManifestError):
            build_manifest(
                agent_id="agent.demo", first_seq=5, last_seq=4,
                previous_segment_head=None, source_schema="mojum.events/1",
                content_sha256=CONTENT, origin_device_id="device.fixture-01",
                created_at_ns=1,
            )

    def test_rejects_modified_head(self):
        data = sample()
        data["created_at_ns"] += 1
        with self.assertRaises(SegmentManifestError):
            validate_manifest(data)

    def test_rejects_uppercase_or_malformed_digest(self):
        data = sample()
        data["content_sha256"] = "A" * 64
        with self.assertRaises(SegmentManifestError):
            validate_manifest(data)

    def test_rejects_duplicate_json_keys(self):
        wire = '{"schema":"x","schema":"y"}'
        with self.assertRaises(SegmentManifestError):
            parse_manifest_json(wire)

    def test_rejects_non_finite_json(self):
        with self.assertRaises(SegmentManifestError):
            parse_manifest_json('{"x":NaN}')

    def test_rejects_oversized_wire(self):
        with self.assertRaises(SegmentManifestError):
            parse_manifest_json(" " * 16385)

    def test_detached_immutable_result(self):
        data = sample()
        result = validate_manifest(data)
        data["agent_id"] = "agent.changed"
        self.assertEqual(result.agent_id, "agent.demo")
        with self.assertRaises(Exception):
            result.agent_id = "agent.changed"


if __name__ == "__main__":
    unittest.main()
