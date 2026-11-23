"""Independent fail-closed object-key QA for Local Cell v4 snapshot admission.

No private journals or devices. The dictionary-key attack is in-memory only.
"""
from copy import deepcopy
import unittest

from mojum.cell_policy_v4 import CellExplorerV4, CellV4Error
from mojum.epoch_sense_v2 import EpochProprioception


class _RehashBomb:
    """Hash once for insertion; fail if a validator re-hashes it."""

    def __init__(self):
        self.hash_calls = 0

    def __hash__(self):
        self.hash_calls += 1
        if self.hash_calls > 1:
            raise AssertionError("untrusted dict key hashed by validator")
        return 123456789

    def __eq__(self, other):
        return self is other


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

    def test_non_native_key_is_rejected_before_rehash_in_every_mapping(self):
        for location in ("outer", "records", "last_seen_at", "actions"):
            with self.subTest(location=location):
                state = deepcopy(_valid_snapshot())
                poison = _RehashBomb()
                if location == "outer":
                    state[poison] = None
                elif location == "records":
                    state["records"][poison] = {"rest": [1, 0]}
                elif location == "last_seen_at":
                    state["last_seen_at"][poison] = 0
                else:
                    cell = next(iter(state["records"]))
                    state["records"][cell][poison] = [1, 0]
                self.assertEqual(poison.hash_calls, 1)
                with self.assertRaises(CellV4Error):
                    CellExplorerV4.from_snapshot(state)
                self.assertEqual(poison.hash_calls, 1)

    def test_string_subclass_cannot_forge_schema_equality(self):
        state = _valid_snapshot()
        state["schema"] = _EqualitySpoof("not-the-v4-schema")
        with self.assertRaises(CellV4Error):
            CellExplorerV4.from_snapshot(state)


if __name__ == "__main__":
    unittest.main()
