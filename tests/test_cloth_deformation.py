"""Adversarial regression tests for the offline cloth deformation auditor."""
import math
import unittest

from mojum.cloth_deformation import (
    MAX_TRIANGLES,
    MAX_VERTICES,
    DeformationAuditError,
    audit_deformation,
)

SQUARE = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))
FACES = ((0, 1, 2), (0, 2, 3))


class ClothDeformationTests(unittest.TestCase):
    def test_unchanged_mesh_has_unit_ratios(self):
        report = audit_deformation(SQUARE, SQUARE, FACES)
        self.assertEqual(report.schema, "mojum.cloth.deformation-audit/1")
        self.assertEqual(report.visibility, "PUBLIC_SYNTHETIC")
        self.assertEqual(report.unique_edge_count, 5)
        self.assertEqual(report.edge_ratio_min, 1.0)
        self.assertEqual(report.edge_ratio_median, 1.0)
        self.assertEqual(report.edge_ratio_p95, 1.0)
        self.assertEqual(report.edge_abs_strain_max, 0.0)
        self.assertEqual(report.area_ratio_median, 1.0)
        self.assertEqual(report.collapsed_triangle_count, 0)
        self.assertEqual(report.orientation_reversal_count, 0)

    def test_uniform_scale_changes_edge_and_area_ratios(self):
        scaled = tuple((x * 2, y * 2, z * 2) for x, y, z in SQUARE)
        report = audit_deformation(SQUARE, scaled, FACES)
        self.assertEqual(report.edge_ratio_min, 2.0)
        self.assertEqual(report.edge_ratio_max, 2.0)
        self.assertEqual(report.area_ratio_min, 4.0)
        self.assertEqual(report.area_ratio_max, 4.0)

    def test_local_stretch_and_compression_are_visible(self):
        moved = ((0, 0, 0), (1.5, 0, 0), (1.5, 0.5, 0), (0, 0.5, 0))
        report = audit_deformation(SQUARE, moved, FACES)
        self.assertGreater(report.edge_ratio_max, 1.0)
        self.assertLess(report.edge_ratio_min, 1.0)
        self.assertGreater(report.edge_abs_strain_max, 0.0)

    def test_translation_preserves_metrics(self):
        moved = tuple((x + 12, y - 3, z + 2) for x, y, z in SQUARE)
        report = audit_deformation(SQUARE, moved, FACES)
        self.assertEqual(report.edge_ratio_min, 1.0)
        self.assertEqual(report.area_ratio_max, 1.0)

    def test_digest_is_deterministic_and_covers_coordinates(self):
        first = audit_deformation(SQUARE, SQUARE, FACES)
        second = audit_deformation([list(v) for v in SQUARE],
                                   [list(v) for v in SQUARE],
                                   [list(f) for f in FACES])
        self.assertEqual(first.input_sha256, second.input_sha256)
        changed = ((0, 0, 0), (1.1, 0, 0), (1, 1, 0), (0, 1, 0))
        self.assertNotEqual(first.input_sha256,
                            audit_deformation(SQUARE, changed, FACES).input_sha256)

    def test_collapsed_current_triangle_is_counted_not_hidden(self):
        collapsed = ((0, 0, 0), (1, 0, 0), (0.5, 0, 0), (0, 1, 0))
        report = audit_deformation(SQUARE, collapsed, FACES)
        self.assertGreaterEqual(report.collapsed_triangle_count, 1)
        self.assertEqual(report.area_ratio_min, 0.0)

    def test_orientation_reversal_is_reported(self):
        flipped = ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))
        report = audit_deformation(SQUARE, flipped, FACES)
        self.assertEqual(report.orientation_reversal_count, 2)

    def test_visibility_is_explicit(self):
        for value in ("PRIVATE", "public_synthetic", None, True):
            with self.subTest(value=value):
                with self.assertRaises(DeformationAuditError):
                    audit_deformation(SQUARE, SQUARE, FACES, visibility=value)

    def test_vertex_count_mismatch_rejected(self):
        with self.assertRaisesRegex(DeformationAuditError, "counts differ"):
            audit_deformation(SQUARE, SQUARE[:-1], FACES)

    def test_invalid_vertex_shapes_rejected(self):
        for vertices in ("abcd", ((0, 0), (1, 0), (0, 1)), ((0, 0, 0),) * 2):
            with self.subTest(vertices=vertices):
                with self.assertRaises(DeformationAuditError):
                    audit_deformation(vertices, vertices, ((0, 1, 2),))

    def test_nonfinite_boolean_and_oversized_coordinates_rejected(self):
        for item in (True, math.nan, math.inf, -math.inf, "0", 1e100, 10**1000):
            with self.subTest(item=str(item)[:16]):
                rest = ((0, 0, 0), (1, 0, 0), (0, 1, 0))
                current = ((0, 0, 0), (item, 0, 0), (0, 1, 0))
                with self.assertRaises(DeformationAuditError):
                    audit_deformation(rest, current, ((0, 1, 2),))

    def test_invalid_triangle_shapes_and_indices_rejected(self):
        for triangles in (("012",), ((0, 1),), ((0, 1, True),),
                          ((0, 1, 4),), ((0, 0, 2),)):
            with self.subTest(triangles=triangles):
                with self.assertRaises(DeformationAuditError):
                    audit_deformation(SQUARE, SQUARE, triangles)

    def test_duplicate_triangle_rejected_independent_of_winding(self):
        with self.assertRaisesRegex(DeformationAuditError, "duplicate triangle"):
            audit_deformation(SQUARE, SQUARE, ((0, 1, 2), (2, 1, 0)))

    def test_degenerate_rest_edge_and_area_rejected(self):
        coincident = ((0, 0, 0), (0, 0, 0), (0, 1, 0))
        with self.assertRaisesRegex(DeformationAuditError, "degenerate edge"):
            audit_deformation(coincident, coincident, ((0, 1, 2),))
        collinear = ((0, 0, 0), (1, 0, 0), (2, 0, 0))
        with self.assertRaisesRegex(DeformationAuditError, "degenerate triangle"):
            audit_deformation(collinear, collinear, ((0, 1, 2),))

    def test_input_budgets_are_enforced(self):
        oversized = SQUARE * (MAX_VERTICES // 4 + 1)
        with self.assertRaisesRegex(DeformationAuditError, "rest_vertices"):
            audit_deformation(oversized, oversized, FACES)
        with self.assertRaisesRegex(DeformationAuditError, "triangles"):
            audit_deformation(SQUARE, SQUARE, FACES * (MAX_TRIANGLES // 2 + 1))


if __name__ == "__main__":
    unittest.main()
