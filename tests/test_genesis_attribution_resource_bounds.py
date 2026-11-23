import unittest

from test_genesis_attribution import sample_snapshot
from mojum.genesis_attribution import AttributionError, audit_attribution


class GenesisAttributionTraversalBudgetTests(unittest.TestCase):
    def test_oversized_list_is_rejected_before_child_expansion(self):
        snapshot = sample_snapshot()
        # Fits beneath the byte ceiling but exceeds the structural node budget.
        snapshot["padding"] = [None] * 45_000
        with self.assertRaisesRegex(AttributionError, "node limit"):
            audit_attribution(snapshot, 0)

    def test_oversized_mapping_is_rejected_before_child_expansion(self):
        snapshot = sample_snapshot()
        # Short keys keep this fixture beneath the byte ceiling.
        snapshot["padding"] = {f"k{i:05d}": None for i in range(17_000)}
        with self.assertRaisesRegex(AttributionError, "node limit"):
            audit_attribution(snapshot, 0)


if __name__ == "__main__":
    unittest.main()
