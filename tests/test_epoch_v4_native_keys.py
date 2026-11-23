"""Independent fail-closed object-key QA for Local Cell v4 snapshot admission.

No private journals or devices. The dictionary-key attack is in-memory only.
"""
from copy import deepcopy
import unittest

from mojum.cell_policy_v4 import CellExplorerV4, CellV4Error
from mojum.epoch_sense_v2 import EpochProprioception


class _EqualityBomb:
    """Collision with a valid string name must not invoke foreign equality."""

    def __init__(self, name):
        self.name = name
        self.equality_calls = 0

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        self.equality_calls += 1
        raise AssertionError("untrusted key equality invoked by validation")


class _EqualitySpoof(str):
    """An invalid schema value which claims equality to every string."""

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False


def _valid_snapshot():
    model = CellExplorerV4()
    cue = EpochProprioception(0, 0, 0, "a" * 64, 0.16, 0.16, False)
    model.observe(cue, model.decide(cue), False)
    return model.snapshot()


class SnapshotNativeKeyAdmissionQA(unittest.TestCase):
    def test_valid_native_snapshot_still_roundtrips(self):
        valid = _valid_snapshot()
        self.assertEqual(CellExplorerV4.from_snapshot(valid).snapshot(), valid)

    def test_non_native_key_rejected_before_hash_collision_equality(self):
        for location in ("outer", "records", "last_seen_at", "actions"):
            with self.subTest(location=location):
                state = deepcopy(_valid_snapshot())
                if location == "outer":
                    mapping, name = state, "schema"
                elif location == "records":
                    mapping = state["records"]
                    name = next(iter(mapping))
                elif location == "last_seen_at":
                    mapping = state["last_seen_at"]
                    name = next(iter(mapping))
                else:
                    cell = next(iter(state["records"]))
                    mapping = state["records"][cell]
                    name = next(iter(mapping))
                value = mapping.pop(name)
                poison = _EqualityBomb(name)
                mapping[poison] = value
                self.assertEqual(poison.equality_calls, 0)
                with self.assertRaises(CellV4Error):
                    CellExplorerV4.from_snapshot(state)
                self.assertEqual(poison.equality_calls, 0)

    def test_string_subclass_cannot_forge_schema_equality(self):
        state = _valid_snapshot()
        state["schema"] = _EqualitySpoof("not-the-v4-schema")
        with self.assertRaises(CellV4Error):
            CellExplorerV4.from_snapshot(state)


if __name__ == "__main__":
    unittest.main()
