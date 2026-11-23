import copy
import hashlib
import json
import unittest

from mojum.noosphere_checkpoint import (
    COMPONENTS, CheckpointError, create_checkpoint, decode_checkpoint,
    encode_checkpoint, verify_checkpoint,
)


def refs(agent="dante", tick=42):
    return {
        name: {
            "schema": f"mojum.{name}/1",
            "revision": f"{name}-r7",
            "content_sha256": hashlib.sha256(name.encode()).hexdigest(),
            "visibility": "PRIVATE",
            "agent_id": agent,
            "tick": tick,
        }
        for name in COMPONENTS
    }


class CheckpointTests(unittest.TestCase):
    def make(self, *, previous=None, components=None):
        return create_checkpoint(
            "dante", "checkpoint-42", 42,
            refs() if components is None else components,
            previous_checkpoint_sha256=previous,
        )

    def test_exact_five_component_manifest_and_digest(self):
        manifest = self.make()
        self.assertEqual(tuple(manifest["components"]), COMPONENTS)
        self.assertEqual(len(manifest["checkpoint_sha256"]), 64)
        self.assertEqual(verify_checkpoint(manifest), manifest)

    def test_component_input_is_not_mutated_or_retained(self):
        source = refs()
        manifest = self.make(components=source)
        source["brain"]["revision"] = "mutated-after-create"
        self.assertEqual(manifest["components"]["brain"]["revision"], "brain-r7")

    def test_missing_component_rejected(self):
        source = refs()
        del source["memory"]
        with self.assertRaises(CheckpointError):
            self.make(components=source)

    def test_unknown_component_rejected(self):
        source = refs()
        source["extra"] = dict(source["brain"])
        with self.assertRaises(CheckpointError):
            self.make(components=source)

    def test_cross_organism_reference_rejected(self):
        source = refs()
        source["body"]["agent_id"] = "other"
        with self.assertRaises(CheckpointError):
            self.make(components=source)

    def test_mismatched_causal_tick_rejected(self):
        source = refs()
        source["world"]["tick"] += 1
        with self.assertRaises(CheckpointError):
            self.make(components=source)

    def test_visibility_and_digest_are_strict(self):
        source = refs()
        source["memory"]["visibility"] = "SECRET"
        with self.assertRaises(CheckpointError):
            self.make(components=source)
        source = refs()
        source["memory"]["content_sha256"] = "A" * 64
        with self.assertRaises(CheckpointError):
            self.make(components=source)

    def test_unhashable_visibility_is_normalized(self):
        source = refs()
        source["memory"]["visibility"] = ["PRIVATE"]
        with self.assertRaises(CheckpointError):
            self.make(components=source)

    def test_boolean_tick_is_not_an_integer(self):
        with self.assertRaises(CheckpointError):
            create_checkpoint("dante", "c", True, refs())

    def test_rejects_hostile_schema_value_before_equality(self):
        class HostileValue:
            def __eq__(self, _other):
                raise RuntimeError("hostile schema equality must not escape")

        manifest = self.make()
        manifest["schema"] = HostileValue()
        with self.assertRaises(CheckpointError):
            verify_checkpoint(manifest)

    def test_manifest_tampering_is_detected(self):
        manifest = self.make()
        tampered = copy.deepcopy(manifest)
        tampered["components"]["brain"]["revision"] = "brain-r8"
        with self.assertRaises(CheckpointError):
            verify_checkpoint(tampered)

    def test_predecessor_cas_accepts_exact_head(self):
        prior = "a" * 64
        manifest = self.make(previous=prior)
        self.assertEqual(
            verify_checkpoint(manifest, expected_previous_checkpoint_sha256=prior),
            manifest,
        )

    def test_predecessor_cas_rejects_stale_or_missing_expectation(self):
        manifest = self.make(previous="a" * 64)
        with self.assertRaises(CheckpointError):
            verify_checkpoint(manifest, expected_previous_checkpoint_sha256="b" * 64)
        with self.assertRaises(CheckpointError):
            verify_checkpoint(manifest)

    def test_genesis_requires_no_predecessor(self):
        manifest = self.make()
        self.assertEqual(
            verify_checkpoint(manifest, expected_previous_checkpoint_sha256=None),
            manifest,
        )

    def test_canonical_wire_round_trip(self):
        manifest = self.make(previous="f" * 64)
        wire = encode_checkpoint(manifest)
        restored = decode_checkpoint(wire)
        self.assertEqual(restored, manifest)
        self.assertEqual(encode_checkpoint(restored), wire)

    def test_hostile_string_subclass_keys_fail_closed(self):
        class HostileKey(str):
            def __hash__(self):
                return hash(str(self))

            def __eq__(self, other):
                raise RuntimeError("hostile key equality must not escape")

        def replace_key(mapping, name):
            result = dict(mapping)
            value = result.pop(name)
            result[HostileKey(name)] = value
            return result

        manifest = self.make()
        hostile_root = replace_key(manifest, "schema")
        with self.assertRaises(CheckpointError):
            verify_checkpoint(hostile_root)

        manifest = self.make()
        hostile_components = dict(manifest["components"])
        hostile_components = replace_key(hostile_components, "brain")
        hostile_manifest = dict(manifest)
        hostile_manifest["components"] = hostile_components
        with self.assertRaises(CheckpointError):
            verify_checkpoint(hostile_manifest)

        source = refs()
        hostile_refs = replace_key(source, "brain")
        with self.assertRaises(CheckpointError):
            self.make(components=hostile_refs)

        source = refs()
        source["brain"] = replace_key(source["brain"], "schema")
        with self.assertRaises(CheckpointError):
            self.make(components=source)

    def test_duplicate_json_keys_rejected(self):
        manifest = self.make()
        wire = json.dumps(manifest, separators=(",", ":"))
        duplicate = wire[:-1] + ',"schema":"forged"}'
        with self.assertRaises(CheckpointError):
            decode_checkpoint(duplicate)

    def test_noncanonical_wire_rejected(self):
        manifest = self.make()
        wire = json.dumps(manifest, indent=2)
        with self.assertRaises(CheckpointError):
            decode_checkpoint(wire)

    def test_oversized_wire_rejected(self):
        with self.assertRaises(CheckpointError):
            decode_checkpoint(b" " * (16 * 1024 + 1))

    def test_wrong_wire_type_rejected(self):
        with self.assertRaises(CheckpointError):
            decode_checkpoint(bytearray(b"{}"))

    def test_no_component_payload_is_embedded(self):
        manifest = self.make()
        for ref in manifest["components"].values():
            self.assertEqual(set(ref), {
                "schema", "revision", "content_sha256",
                "visibility", "agent_id", "tick",
            })


if __name__ == "__main__":
    unittest.main()
