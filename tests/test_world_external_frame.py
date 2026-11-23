"""Offline external-world observation admission: no network or device use."""
import copy
import hashlib
import json
import unittest

from mojum.world_external_frame import (
    ADMISSION_SCHEMA, CAPABILITY, FRAME_SCHEMA, ExternalFrameGrant,
    admit_external_frame, verify_external_admission,
)

REVISION = "ab" * 32
_UNSET = object()


class ExternalFrameTests(unittest.TestCase):
    def setUp(self):
        self.grant = ExternalFrameGrant("game_fixture", "example.game", "level01", 20_000_000)

    def frame(self, tick=0, sequence=0):
        return {
            "schema": FRAME_SCHEMA, "audience": "PUBLIC_SYNTHETIC",
            "synthetic": True, "source_kind": "game_fixture",
            "source_id": "example.game", "world_id": "level01",
            "capability": CAPABILITY, "source_revision": REVISION,
            "tick": tick, "time_ns": tick * 20_000_000,
            "sequence": sequence, "physical_contact": False,
            "physical_actuation": False,
            "objects": [{"id": "npc-1", "x_m": 1, "y_m": 1.25, "visible": True}],
        }

    def admit(self, frame=_UNSET, previous=None, tick=0):
        return admit_external_frame(self.frame(tick, tick) if frame is _UNSET else frame,
                                    grant=self.grant, expected_tick=tick,
                                    previous_admission=previous)

    def test_genesis_and_replay_are_reproducible(self):
        original = self.frame()
        first = self.admit(original)
        self.assertEqual(first["schema"], ADMISSION_SCHEMA)
        self.assertEqual(first, self.admit(original))
        self.assertEqual(verify_external_admission(first, grant=self.grant, expected_tick=0), first)
        json.dumps(first, allow_nan=False)
        original["objects"][0]["x_m"] = 99
        self.assertEqual(first["frame"]["objects"][0]["x_m"], 1)
        self.assertIsNone(first["parent_sha256"])

    def test_contiguous_chain_and_replay_rejected(self):
        first = self.admit()
        second = self.admit(previous=first, tick=1)
        self.assertEqual(second["parent_sha256"], first["admission_sha256"])
        self.assertEqual(verify_external_admission(second, grant=self.grant,
                         expected_tick=1, previous_admission=first), second)
        for old_sequence in (0, 2, -1, True):
            frame = self.frame(1, old_sequence)
            with self.subTest(sequence=old_sequence), self.assertRaises(ValueError):
                self.admit(frame, previous=first, tick=1)
        with self.assertRaises(ValueError):
            self.admit(self.frame(2, 2), previous=first, tick=2)
        with self.assertRaises(ValueError):
            self.admit(self.frame(1, 1), tick=1)

    def test_bad_schema_audience_and_physical_flags(self):
        for key, value in (("schema", "legacy"), ("audience", "PRIVATE"),
                           ("synthetic", 1), ("physical_contact", 0),
                           ("physical_actuation", 0), ("capability", "world.actuate"),
                           ("source_kind", "real_device"), ("source_id", "attacker"),
                           ("world_id", "other")):
            frame = self.frame()
            frame[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.admit(frame)

    def test_malformed_tick_timestamp_and_revision(self):
        for key, value in (("tick", True), ("tick", -1), ("tick", 1),
                           ("time_ns", 1), ("time_ns", 0.0),
                           ("sequence", 1), ("sequence", 1.0),
                           ("source_revision", "F"*64), ("source_revision", "0"*63)):
            frame = self.frame()
            frame[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.admit(frame)

    def test_invalid_object_shape_and_numbers(self):
        for key, value in (("x_m", float("nan")), ("x_m", float("inf")),
                           ("x_m", 10001), ("x_m", 10**1000),
                           ("y_m", True), ("visible", 1),
                           ("id", "é"), ("id", "")):
            frame = self.frame()
            frame["objects"][0][key] = value
            with self.subTest(key=key, value=str(value)[:30]), self.assertRaises(ValueError):
                self.admit(frame)
        frame = self.frame()
        frame["objects"] *= 65
        with self.assertRaises(ValueError):
            self.admit(frame)
        frame = self.frame()
        frame["objects"].append(dict(frame["objects"][0]))
        with self.assertRaises(ValueError):
            self.admit(frame)
        frame = self.frame()
        frame["objects"][0]["secret"] = "PRIVATE"
        with self.assertRaises(ValueError):
            self.admit(frame)

    def test_strict_json_shapes_and_no_extra_fields(self):
        for bad in (None, [], {}, {**self.frame(), "host": "x"}):
            with self.subTest(bad=type(bad).__name__), self.assertRaises(ValueError):
                self.admit(bad)
        frame = self.frame()
        frame["objects"] = ()
        with self.assertRaises(ValueError):
            self.admit(frame)
        frame = self.frame()
        frame["objects"][0] = {"id": "x", "x_m": 0, "y_m": 0, "visible": True, 7: 3}
        with self.assertRaises(ValueError):
            self.admit(frame)

    def test_rehashed_predecessor_still_needs_valid_lineage(self):
        # Hash-consistent receipts are NOT authenticated; even their own shape
        # must be rejected unless genesis/non-genesis lineage is coherent.
        def rehash(receipt):
            def digest(value):
                wire = json.dumps(value, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=True, allow_nan=False).encode("ascii")
                return hashlib.sha256(wire).hexdigest()

            receipt["frame_sha256"] = digest(receipt["frame"])
            receipt["admission_sha256"] = digest({
                key: value for key, value in receipt.items()
                if key != "admission_sha256"
            })
            return receipt

        first = self.admit()
        second = self.admit(previous=first, tick=1)
        third = self.admit(previous=second, tick=2)
        self.assertEqual(third["parent_sha256"], second["admission_sha256"])

        forged_genesis = copy.deepcopy(first)
        forged_genesis["parent_sha256"] = "a" * 64
        rehash(forged_genesis)
        with self.assertRaisesRegex(ValueError, "predecessor"):
            self.admit(previous=forged_genesis, tick=1)

        orphan_second = copy.deepcopy(second)
        orphan_second["parent_sha256"] = None
        rehash(orphan_second)
        with self.assertRaisesRegex(ValueError, "predecessor"):
            self.admit(previous=orphan_second, tick=2)

        wrong_sequence = copy.deepcopy(first)
        wrong_sequence["frame"]["sequence"] = 7
        rehash(wrong_sequence)
        with self.assertRaisesRegex(ValueError, "predecessor"):
            self.admit(self.frame(1, 8), previous=wrong_sequence, tick=1)

    def test_tampered_previous_digest_and_frame(self):
        first = self.admit()
        for path, value in (("frame_sha256", "0" * 64), ("admission_sha256", "0" * 64),
                            ("parent_sha256", "0" * 64)):
            prior = copy.deepcopy(first)
            prior[path] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.admit(previous=prior, tick=1)
        prior = copy.deepcopy(first)
        prior["frame"]["objects"][0]["x_m"] = 10
        with self.assertRaises(ValueError):
            self.admit(previous=prior, tick=1)

    def test_tampered_admission_rejected(self):
        first = self.admit()
        for path, value in (("frame_sha256", "0" * 64),
                            ("admission_sha256", "0" * 64),
                            ("parent_sha256", "0" * 64)):
            changed = copy.deepcopy(first)
            changed[path] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                verify_external_admission(changed, grant=self.grant, expected_tick=0)
        changed = copy.deepcopy(first)
        changed["frame"]["objects"][0]["x_m"] = 1.5
        with self.assertRaises(ValueError):
            verify_external_admission(changed, grant=self.grant, expected_tick=0)

    def test_rejects_adversarial_type_equality_before_comparison(self):
        class Evil:
            def __eq__(self, other):
                raise AssertionError("equality method executed")
        for key in ("schema", "audience", "source_kind", "source_id",
                    "world_id", "capability", "source_revision"):
            frame = self.frame()
            frame[key] = Evil()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.admit(frame)
        frame = self.frame()
        frame["objects"][0]["id"] = Evil()
        with self.assertRaises(ValueError):
            self.admit(frame)

    def test_invalid_grant_and_time_bounds(self):
        for kwargs in ({"source_kind": "game_fixture", "source_id": "x",
                        "world_id": "w", "tick_period_ns": 0},
                       {"source_kind": "game_fixture", "source_id": "x",
                        "world_id": "w", "tick_period_ns": True},
                       {"source_kind": "vr_real", "source_id": "x",
                        "world_id": "w", "tick_period_ns": 1},
                       {"source_kind": "vr_fixture", "source_id": "bad/name",
                        "world_id": "w", "tick_period_ns": 1}):
            with self.subTest(grant=kwargs), self.assertRaises(ValueError):
                ExternalFrameGrant(**kwargs)
        with self.assertRaises(ValueError):
            admit_external_frame(self.frame(), grant=None, expected_tick=0)
        with self.assertRaises(ValueError):
            self.admit(tick=True)

    def test_empty_scene_supported_and_frame_order_significant(self):
        frame = self.frame()
        frame["objects"] = []
        self.assertEqual(self.admit(frame)["frame"]["objects"], [])
        first = self.frame()
        first["objects"].append({"id": "npc-2", "x_m": -1, "y_m": 0, "visible": False})
        second = copy.deepcopy(first)
        second["objects"].reverse()
        self.assertNotEqual(self.admit(first)["frame_sha256"], self.admit(second)["frame_sha256"])


if __name__ == "__main__":
    unittest.main()
