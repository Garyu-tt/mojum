import dataclasses
import unittest

from mojum.cloth_seams import ClothSeamError, validate_seams


def fixture():
    # Two disconnected unit-square panels separated by a 1 mm seam gap.
    vertices = [
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0],
        [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [1.001, 0.0, 0.0], [2.001, 0.0, 0.0],
        [2.001, 1.0, 0.0], [1.001, 1.0, 0.0],
    ]
    triangles = [[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]]
    seams = [{"id": "side-left", "pairs": [[1, 4], [2, 7]]}]
    return vertices, triangles, seams


class ClothSeamTests(unittest.TestCase):
    def admit(self, vertices=None, triangles=None, seams=None, **kwargs):
        v, t, s = fixture()
        return validate_seams(
            v if vertices is None else vertices,
            t if triangles is None else triangles,
            s if seams is None else seams,
            provenance="PUBLIC_SYNTHETIC",
            **kwargs,
        )

    def test_valid_two_panel_seam(self):
        report = self.admit()
        self.assertEqual(report.schema, "mojum.cloth-seams/1")
        self.assertEqual(report.panel_count, 2)
        self.assertEqual(report.boundary_edge_count, 8)
        self.assertEqual(report.seam_count, 1)
        self.assertEqual(report.stitch_count, 2)
        self.assertAlmostEqual(report.max_gap_m, 0.001)
        self.assertEqual(report.seams[0].seam_id, "side-left")
        self.assertEqual(len(report.sha256), 64)

    def test_replay_is_deterministic(self):
        first = self.admit()
        second = self.admit()
        self.assertEqual(first, second)

    def test_reports_sort_by_id(self):
        v, t, _ = fixture()
        # Third disconnected triangular panel gives a second, non-overlapping seam.
        v.extend([[0.001, 0.0, 0.0], [0.001, 1.0, 0.0], [-0.1, 0.5, 0.0]])
        t.append([8, 9, 10])
        seams = [
            {"id": "z-side", "pairs": [[1, 4], [2, 7]]},
            {"id": "a-side", "pairs": [[0, 8], [3, 9]]},
        ]
        result = self.admit(vertices=v, triangles=t, seams=seams)
        self.assertEqual([x.seam_id for x in result.seams], ["a-side", "z-side"])

    def test_report_is_immutable(self):
        report = self.admit()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            report.stitch_count = 99
        with self.assertRaises(dataclasses.FrozenInstanceError):
            report.seams[0].stitch_count = 99

    def test_provenance_is_mandatory_and_exact(self):
        v, t, s = fixture()
        with self.assertRaises(ClothSeamError):
            validate_seams(v, t, s, provenance="PRIVATE")
        with self.assertRaises(ClothSeamError):
            validate_seams(v, t, s, provenance="PUBLIC_SYNTHETIC ")

    def test_bool_is_not_an_index(self):
        v, t, s = fixture()
        t[0][0] = True
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=s)

    def test_bool_is_not_a_coordinate(self):
        v, _, _ = fixture()
        v[0][0] = True
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v)

    def test_excessive_gap_rejected(self):
        v, t, s = fixture()
        v[4][0] = 1.1
        v[7][0] = 1.1
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=s, max_gap_m=0.01)

    def test_gap_budget_is_bounded(self):
        with self.assertRaises(ClothSeamError):
            self.admit(max_gap_m=0.250001)

    def test_non_boundary_endpoint_rejected(self):
        v, t, s = fixture()
        s[0]["pairs"] = [[0, 4], [2, 7]]
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=s)

    def test_discontinuous_path_rejected(self):
        v, t, s = fixture()
        s[0]["pairs"] = [[1, 4], [3, 7]]
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=s)

    def test_reused_vertex_rejected(self):
        v, t, _ = fixture()
        seams = [{"id": "one", "pairs": [[1, 4], [2, 7]]},
                 {"id": "two", "pairs": [[1, 4], [0, 7]]}]
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=seams)

    def test_duplicate_seam_id_rejected(self):
        v, t, _ = fixture()
        seams = [{"id": "same", "pairs": [[1, 4], [2, 7]]},
                 {"id": "same", "pairs": [[0, 4], [3, 7]]}]
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=seams)

    def test_wrong_panel_pairing_rejected(self):
        v, t, _ = fixture()
        seams = [{"id": "bad", "pairs": [[1, 4], [2, 3]]}]
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=seams)

    def test_duplicate_face_rejected(self):
        v, t, s = fixture()
        t.append([2, 1, 0])
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=s)

    def test_degenerate_face_rejected(self):
        v, t, s = fixture()
        t[0] = [0, 1, 1]
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=s)

    def test_non_manifold_edge_rejected(self):
        v, t, s = fixture()
        v.extend([[0.5, -1.0, 0.0]])
        t.append([0, 2, 8])
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=s)

    def test_unreferenced_vertex_rejected(self):
        v, t, s = fixture()
        v.append([9.0, 9.0, 9.0])
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=s)

    def test_invalid_pair_shape_rejected(self):
        v, t, s = fixture()
        s[0]["pairs"][0] = [1, 4, 2]
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=s)

    def test_pair_count_minimum(self):
        v, t, s = fixture()
        s[0]["pairs"] = [[1, 4]]
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=s)

    def test_extra_seam_field_rejected(self):
        v, t, s = fixture()
        s[0]["unexpected"] = True
        with self.assertRaises(ClothSeamError):
            self.admit(vertices=v, triangles=t, seams=s)


if __name__ == "__main__":
    unittest.main()
