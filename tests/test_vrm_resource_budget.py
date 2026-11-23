import unittest
from mojum.vrm_resource_budget import ResourceBudgetError, ResourceLimits, SCHEMA, assess_vrm_resource_budget

def inventory(**changes):
    data = {"schema": SCHEMA, "vertex_count": 100, "index_count": 300,
            "material_count": 2, "texture_bytes": 1024, "joint_count": 24}
    data.update(changes)
    return data

class VrmResourceBudgetTests(unittest.TestCase):
    def test_small_inventory_accepted(self):
        self.assertEqual(assess_vrm_resource_budget(inventory()).decision, "accept")
    def test_estimate_is_transparent(self):
        report = assess_vrm_resource_budget(inventory())
        self.assertEqual(report.estimated_bytes, 100*32 + 300*4 + 2*256 + 1024 + 24*256)
    def test_digest_is_order_independent(self):
        first = inventory()
        self.assertEqual(assess_vrm_resource_budget(first).inventory_sha256,
                         assess_vrm_resource_budget(dict(reversed(list(first.items())))).inventory_sha256)
    def test_soft_limit_rejects(self):
        report = assess_vrm_resource_budget(inventory(vertex_count=250001))
        self.assertEqual(report.decision, "reject")
        self.assertIn("vertex_count", report.exceeded)
    def test_eighty_percent_warns(self):
        report = assess_vrm_resource_budget(inventory(vertex_count=850), ResourceLimits(max_vertices=1000))
        self.assertEqual(report.decision, "warn")
    def test_exact_limit_accepted(self):
        report = assess_vrm_resource_budget(inventory(), ResourceLimits(max_vertices=100))
        self.assertNotIn("vertex_count", report.exceeded)
    def test_bool_rejected(self):
        with self.assertRaises(ResourceBudgetError):
            assess_vrm_resource_budget(inventory(vertex_count=True))
    def test_negative_rejected(self):
        with self.assertRaises(ResourceBudgetError):
            assess_vrm_resource_budget(inventory(joint_count=-1))
    def test_hard_ceiling_rejected(self):
        with self.assertRaises(ResourceBudgetError):
            assess_vrm_resource_budget(inventory(texture_bytes=512*1024*1024+1))
    def test_triangle_count_required(self):
        with self.assertRaises(ResourceBudgetError):
            assess_vrm_resource_budget(inventory(index_count=301))
    def test_extra_field_rejected(self):
        with self.assertRaises(ResourceBudgetError):
            assess_vrm_resource_budget({**inventory(), "gpu_bytes": 1})
    def test_wrong_schema_rejected(self):
        with self.assertRaises(ResourceBudgetError):
            assess_vrm_resource_budget(inventory(schema="wrong"))
    def test_non_dict_rejected(self):
        with self.assertRaises(ResourceBudgetError):
            assess_vrm_resource_budget([])
    def test_limits_bool_rejected(self):
        with self.assertRaises(ResourceBudgetError):
            ResourceLimits(max_vertices=True)
    def test_rejects_oversized_integer_before_serialization(self):
        with self.assertRaises(ResourceBudgetError):
            assess_vrm_resource_budget(inventory(vertex_count=10 ** 100_000))

    def test_revalidates_mutated_frozen_limits(self):
        limits = ResourceLimits()
        object.__setattr__(limits, "max_vertices", -1)
        with self.assertRaises(ResourceBudgetError):
            assess_vrm_resource_budget(inventory(), limits)

    def test_rejects_nested_value_without_serializing_it(self):
        with self.assertRaises(ResourceBudgetError):
            assess_vrm_resource_budget(inventory(vertex_count={"nested": [1]}))

    def test_report_detached(self):
        data = inventory()
        report = assess_vrm_resource_budget(data)
        data["vertex_count"] = 999
        self.assertEqual(dict(report.counts)["vertex_count"], 100)

if __name__ == "__main__":
    unittest.main()
