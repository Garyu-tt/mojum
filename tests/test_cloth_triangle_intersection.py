import unittest

from mojum.cloth_triangle_intersection import TriangleIntersectionError, audit_triangle_intersections


class TriangleIntersectionTests(unittest.TestCase):
    def test_crossing_non_coplanar_triangles(self):
        vertices = [(0,0,0),(2,0,0),(0,2,0),(0.5,0.5,-1),(0.5,0.5,1),(1.5,0.5,0)]
        self.assertEqual(audit_triangle_intersections(vertices, [(0,1,2),(3,4,5)], [(0,1)]).intersection_pairs, ((0,1),))

    def test_separated_triangles(self):
        vertices = [(0,0,0),(1,0,0),(0,1,0),(0,0,2),(1,0,2),(0,1,2)]
        self.assertEqual(audit_triangle_intersections(vertices, [(0,1,2),(3,4,5)], [(1,0)]).intersection_pairs, ())

    def test_coplanar_overlap(self):
        vertices = [(0,0,0),(2,0,0),(0,2,0),(0.5,0.5,0),(2,0.5,0),(0.5,2,0)]
        self.assertEqual(audit_triangle_intersections(vertices, [(0,1,2),(3,4,5)], [(0,1)]).intersection_pairs, ((0,1),))

    def test_coplanar_disjoint(self):
        vertices = [(0,0,0),(1,0,0),(0,1,0),(2,2,0),(3,2,0),(2,3,0)]
        self.assertEqual(audit_triangle_intersections(vertices, [(0,1,2),(3,4,5)], [(0,1)]).intersection_pairs, ())

    def test_shared_vertex_pair_is_ignored(self):
        vertices = [(0,0,0),(1,0,0),(0,1,0),(0,0,1),(1,1,1)]
        report = audit_triangle_intersections(vertices, [(0,1,2),(0,3,4)], [(0,1)])
        self.assertEqual((report.skipped_adjacent, report.tested_count), (1,0))

    def test_boundary_touch_counts(self):
        vertices = [(0,0,0),(1,0,0),(0,1,0),(1,0,0),(2,0,0),(1,1,0)]
        self.assertEqual(audit_triangle_intersections(vertices, [(0,1,2),(3,4,5)], [(0,1)]).intersection_pairs, ((0,1),))

    def test_small_separated_triangles_are_not_coplanar(self):
        vertices = [(0,0,0),(1e-5,0,0),(0,1e-5,0),
                    (0,0,1e-6),(1e-5,0,1e-6),(0,1e-5,1e-6)]
        report = audit_triangle_intersections(vertices, [(0,1,2),(3,4,5)], [(0,1)])
        self.assertEqual(report.intersection_pairs, ())

    def test_tiny_triangles_far_apart_do_not_collapse_under_pair_distance_scale(self):
        vertices = [
            (0, 0, 0), (1e-5, 0, 0), (0, 1e-5, 0),
            (1e9, 0, 1e-6), (1e9 + 1e-5, 0, 1e-6),
            (1e9, 1e-5, 1e-6),
        ]
        report = audit_triangle_intersections(vertices, [(0,1,2), (3,4,5)], [(0,1)])
        self.assertEqual(report.intersection_pairs, ())

    def test_scale_equivalent_intersections_and_separations(self):
        for scale in (1e-5, 1.0, 1e5):
            with self.subTest(scale=scale):
                crossing = [(0,0,0),(2*scale,0,0),(0,2*scale,0),
                            (0.5*scale,0.5*scale,-scale),(0.5*scale,0.5*scale,scale),(1.5*scale,0.5*scale,0)]
                separated = [(0,0,0),(scale,0,0),(0,scale,0),
                             (0,0,0.1*scale),(scale,0,0.1*scale),(0,scale,0.1*scale)]
                self.assertEqual(audit_triangle_intersections(crossing, [(0,1,2),(3,4,5)], [(0,1)]).intersection_pairs, ((0,1),))
                self.assertEqual(audit_triangle_intersections(separated, [(0,1,2),(3,4,5)], [(0,1)]).intersection_pairs, ())

    def test_duplicate_pair_rejected(self):
        vertices = [(0,0,0),(1,0,0),(0,1,0),(0,0,2),(1,0,2),(0,1,2)]
        with self.assertRaises(TriangleIntersectionError):
            audit_triangle_intersections(vertices, [(0,1,2),(3,4,5)], [(0,1),(1,0)])

    def test_degenerate_triangle_rejected(self):
        vertices = [(0,0,0),(1,0,0),(2,0,0),(0,0,2),(1,0,2),(0,1,2)]
        with self.assertRaises(TriangleIntersectionError):
            audit_triangle_intersections(vertices, [(0,1,2),(3,4,5)], [(0,1)])

    def test_hostile_types_rejected(self):
        vertices = [(0,0,0),(1,0,0),(0,1,0),(0,0,2),(1,0,2),(0,1,2)]
        with self.assertRaises(TriangleIntersectionError):
            audit_triangle_intersections(vertices, [(0,1,2),(3,4,5)], [(False,1)])

    def test_oversized_integer_coordinate_uses_validation_error(self):
        vertices = [(0,0,0),(1,0,0),(0,1,0),(0,0,2),(1,0,2),(0,1,2)]
        vertices[0] = (10**1000, 0, 0)
        with self.assertRaises(TriangleIntersectionError):
            audit_triangle_intersections(vertices, [(0,1,2),(3,4,5)], [(0,1)])

    def test_digest_binds_geometry_not_only_result_counts(self):
        vertices = [(0,0,0),(1,0,0),(0,1,0),(0,0,2),(1,0,2),(0,1,2)]
        faces = [(0,1,2),(3,4,5)]
        a = audit_triangle_intersections(vertices, faces, [(0,1)])
        changed = list(vertices)
        changed[3] = (0,0,3)
        b = audit_triangle_intersections(changed, faces, [(0,1)])
        self.assertNotEqual(a.digest_sha256, b.digest_sha256)

    def test_deterministic_digest_and_order(self):
        vertices = [(0,0,0),(2,0,0),(0,2,0),(0.5,0.5,-1),(0.5,0.5,1),(1.5,0.5,0),(0,0,3),(1,0,3),(0,1,3)]
        triangles = [(0,1,2),(3,4,5),(6,7,8)]
        a = audit_triangle_intersections(vertices, triangles, [(1,0),(2,0)])
        b = audit_triangle_intersections(vertices, triangles, [(0,2),(0,1)])
        self.assertEqual(a, b)
        self.assertEqual(len(a.digest_sha256), 64)


if __name__ == "__main__":
    unittest.main()
