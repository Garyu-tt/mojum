"""Synthetic stdlib-only tests; never load native code or private journals."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from mojum.crsqlite_collab import (AUDIENCE, SCHEMA, MAX_OPS, MetadataBoundaryError,
                                   native_partition_probe, reduce_fixture, validate_operation,
                                   _checked_native_projection)


def op(replica="alice", counter=1, key="label.box", value="start", kind="put", **extra):
    data = {"schema": SCHEMA, "audience": AUDIENCE, "workspace": "demo_board",
            "replica": replica, "counter": counter, "key": key,
            "kind": kind, "value": value}
    data.update(extra)
    return data


class CRSQLiteMetadataFixtureTests(unittest.TestCase):
    def test_synthetic_partition_converges_under_reordering(self):
        changes = [op("alice", 1, value="left"), op("bob", 1, value="right"),
                   op("alice", 2, key="layout.node_1", value="10,20")]
        one = reduce_fixture(changes)
        other = reduce_fixture(list(reversed(changes)) + changes)
        self.assertEqual(one, other)
        self.assertEqual(one["entries"], {"label.box": "right", "layout.node_1": "10,20"})

    def test_tombstone_stops_resurrection(self):
        changes = [op("alice", 1, value="old"),
                   op("bob", 2, kind="remove", value=None)]
        self.assertEqual(reduce_fixture(changes)["entries"], {})
        self.assertEqual(reduce_fixture(list(reversed(changes)))["entries"], {})

    def test_rejects_private_or_extra_fields(self):
        for kwargs in ({"audience": "PRIVATE"}, {"secret": "token"},
                       {"source": "noosphere"}, {"workspace": "live_organism"},
                       {"workspace": "demo_../bad"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(MetadataBoundaryError):
                validate_operation(op(**kwargs))

    def test_schema_and_audience_reject_equality_spoofing(self):
        class SpoofTag:
            def __eq__(self, other):
                return other in (SCHEMA, AUDIENCE)

        for field in ("schema", "audience"):
            with self.subTest(field=field), self.assertRaises(MetadataBoundaryError):
                validate_operation(op(**{field: SpoofTag()}))

    def test_kind_rejects_hostile_equality_without_invocation(self):
        class HostileKind:
            def __eq__(self, other):
                raise AssertionError("untrusted kind equality must not run")

        class HostileString(str):
            def __eq__(self, other):
                raise AssertionError("str subclass equality must not run")

        for kind in (HostileKind(), HostileString("put"), None, True, 1):
            with self.subTest(kind=type(kind).__name__):
                with self.assertRaises(MetadataBoundaryError):
                    validate_operation(op(kind=kind))

    def test_native_output_rejects_nonpublic_cells(self):
        self.assertEqual(_checked_native_projection({"label.good": "public ASCII"}),
                         {"label.good": "public ASCII"})
        for data in ({"label.good": ""}, {"label.good": "secret\nvalue"},
                     {"note.a": "snowman \u2603"}, {"layout.x": "x" * 257},
                     {"motor.gate": "x"}, {123: "numeric-key"}):
            with self.subTest(data=data), self.assertRaises(MetadataBoundaryError):
                _checked_native_projection(data)

    def test_rejects_actuator_and_authority_namespaces(self):
        for key in ("brain.plan", "journal.head", "lease.owner", "motor.gate", "node.addr",
                    "auth.token", "graph.node", "label/../../x", "layout."):
            with self.subTest(key=key), self.assertRaises(MetadataBoundaryError):
                validate_operation(op(key=key))

    def test_rejects_type_confusion_and_control_bytes(self):
        for kwargs in ({"counter": True}, {"counter": -1}, {"counter": 2**50},
                       {"value": "x" * 257}, {"value": "line\nsecret"},
                       {"value": "\u2603"}, {"kind": "remove", "value": "x"},
                       {"kind": "put", "value": None}, {"replica": "../alice"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(MetadataBoundaryError):
                validate_operation(op(**kwargs))

    def test_rejects_equivocation(self):
        with self.assertRaisesRegex(MetadataBoundaryError, "equivocation"):
            reduce_fixture([op("a", 1, value="one"), op("a", 1, value="two")])

    def test_rejects_cross_workspace(self):
        with self.assertRaisesRegex(MetadataBoundaryError, "cross-workspace"):
            reduce_fixture([op(), op("bob", 1, workspace="demo_other")])

    def test_rejects_batch_over_limit(self):
        with self.assertRaises(MetadataBoundaryError):
            reduce_fixture([op("a", n + 1) for n in range(MAX_OPS + 1)])

    def test_immutability_and_sha256(self):
        value = op()
        copied = validate_operation(value)
        copied["value"] = "modified"
        self.assertEqual(value["value"], "start")
        projected = reduce_fixture([value])
        self.assertEqual(len(projected["sha256"]), 64)

    def test_native_opt_in_preflight_no_extension(self):
        with self.assertRaisesRegex(MetadataBoundaryError, "absolute"):
            native_partition_probe("crsqlite.so", "0" * 64, [op()], [op("bob")])

    def test_native_rejects_same_actor_before_loading_extension(self):
        with self.assertRaisesRegex(MetadataBoundaryError, "distinct"):
            native_partition_probe("/bad/crsqlite.so", "0" * 64, [op()], [op("alice", 2)])

    def test_native_preflight_rejects_wrong_hash_and_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            artifact = Path(d) / "crsqlite.so"
            artifact.write_bytes(b"not a binary")
            with self.assertRaisesRegex(MetadataBoundaryError, "SHA-256 mismatch"):
                native_partition_probe(str(artifact), "0" * 64, [op()], [op("bob")])
            link = Path(d) / "link.so"
            link.symlink_to(artifact)
            with self.assertRaisesRegex(MetadataBoundaryError, "unsafe"):
                native_partition_probe(str(link), hashlib.sha256(b"not a binary").hexdigest(),
                                       [op()], [op("bob")])

    def test_native_rejects_world_writable_binary(self):
        with tempfile.TemporaryDirectory() as d:
            artifact = Path(d) / "crsqlite.so"
            artifact.write_bytes(b"not a binary")
            artifact.chmod(0o666)
            with self.assertRaisesRegex(MetadataBoundaryError, "unsafe"):
                native_partition_probe(str(artifact), hashlib.sha256(b"not a binary").hexdigest(),
                                       [op()], [op("bob")])

    def test_native_rejects_tampered_schema_before_extension(self):
        with self.assertRaises(MetadataBoundaryError):
            native_partition_probe("/bad/crsqlite.so", "0" * 64,
                                   [op(source="real_identity")], [op("bob")])


if __name__ == "__main__":
    unittest.main()
