"""Adversarial stdlib tests for opt-in synthetic cloth mesh admission."""
import math
import unittest

from mojum.cloth_topology import (
    MAX_TRIANGLES, MAX_VERTICES, audit_cloth_mesh,
)


SQUARE = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))
FACES = ((0, 1, 2), (0, 2, 3))


class ClothTopologyTest(unittest.TestCase):
    def reject(self, vertices, faces, error, **kwargs):
        with self.assertRaisesRegex(ValueError, error):
            audit_cloth_mesh(vertices, faces, **kwargs)

    def test_open_square_and_digest_replay(self):
        one = audit_cloth_mesh(SQUARE, FACES)
        two = audit_cloth_mesh([list(v) for v in SQUARE], [list(f) for f in FACES])
        self.assertEqual(one, two)
        self.assertEqual((one.vertex_count, one.triangle_count, one.edge_count,
                          one.boundary_edge_count, one.boundary_loop_count,
                          one.component_count), (4, 2, 5, 4, 1, 1))
        self.assertEqual(len(one.sha256), 64)

    def test_closed_tetrahedron(self):
        v = ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))
        t = ((0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3))
        r = audit_cloth_mesh(v, t)
        self.assertEqual((r.edge_count, r.boundary_loop_count), (6, 0))

    def test_winding_failure(self):
        self.reject(SQUARE, ((0, 1, 2), (0, 3, 2)), "winding")

    def test_edge_triple_incidence(self):
        vertices = SQUARE + ((0.5, -1, 0),)
        self.reject(vertices, ((0, 1, 2), (1, 0, 3), (0, 1, 4)), "non-manifold edge")

    def test_closed_bowtie_vertex(self):
        # Two tetrahedra touching only at vertex 0: all edges have two faces.
        v = ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1),
             (-1, 0, 0), (0, -1, 0), (0, 0, -1))
        f = ((0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3),
             (0, 5, 4), (0, 4, 6), (4, 5, 6), (5, 0, 6))
        self.reject(v, f, "disconnected vertex fan", require_connected=False)

    def test_disconnected_panels_can_be_explicit(self):
        v = SQUARE + tuple((x + 4, y, z) for x, y, z in SQUARE)
        f = FACES + ((4, 5, 6), (4, 6, 7))
        self.reject(v, f, "disconnected cloth panels")
        r = audit_cloth_mesh(v, f, require_connected=False)
        self.assertEqual((r.component_count, r.boundary_loop_count), (2, 2))

    def test_duplicate_triangle_ignore_winding(self):
        self.reject(SQUARE, ((0, 1, 2), (2, 1, 0)), "duplicate triangle")

    def test_duplicate_vertex_positions(self):
        self.reject(SQUARE + ((1, 0, 0),), FACES, "duplicate vertex")

    def test_orphan_vertex(self):
        self.reject(SQUARE, ((0, 1, 2),), "isolated vertex")

    def test_collinear(self):
        self.reject(((0, 0, 0), (1, 0, 0), (2, 0, 0)), ((0, 1, 2),), "area")

    def test_non_integer_indices_and_bool(self):
        for item in (True, 1.0, "1", None):
            with self.subTest(item=item):
                self.reject(SQUARE, ((0, item, 2),), "indices")

    def test_hostile_coordinates(self):
        for item in (math.nan, math.inf, True, "0", 1e100, 10**1000):
            with self.subTest(item=str(item)[:20]):
                self.reject(((0, 0, 0), (item, 0, 0), (0, 1, 0)),
                            ((0, 1, 2),), "coordinate")

    def test_bounded_records_before_iteration(self):
        self.reject(SQUARE * (MAX_VERTICES // 4 + 1), FACES, "vertex collection")
        self.reject(SQUARE, FACES * (MAX_TRIANGLES // 2 + 1), "triangle collection")

    def test_invalid_shapes_and_controls(self):
        self.reject("123", FACES, "vertex collection")
        self.reject(SQUARE, ("012",), "triangle indices")
        self.reject(SQUARE, FACES, "require_connected", require_connected=1)
        self.reject(((0, 0, 0), (1, 0, 0), (0, 1)), ((0, 1, 2),), "vertex shape")

    def test_digest_reflects_winding_and_positions(self):
        a = audit_cloth_mesh(SQUARE, FACES)
        b = audit_cloth_mesh(SQUARE, ((0, 2, 1), (0, 3, 2)))
        moved = ((0, 0, 0), (2, 0, 0), (1, 1, 0), (0, 1, 0))
        c = audit_cloth_mesh(moved, FACES)
        self.assertNotEqual(a.sha256, b.sha256)
        self.assertNotEqual(a.sha256, c.sha256)


if __name__ == "__main__":
    unittest.main()
