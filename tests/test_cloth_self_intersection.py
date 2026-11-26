import unittest
from mojum.cloth_self_intersection import audit_self_intersection_candidates, SCHEMA


class ClothSelfIntersectionTests(unittest.TestCase):
    def test_overlapping_non_adjacent_faces(self):
        v = [(0,0,0),(2,0,0),(0,2,0),(0.2,0.2,0),(1.8,0.2,0),(0.2,1.8,0)]
        r = audit_self_intersection_candidates(v, [(0,1,2),(3,4,5)], provenance="PUBLIC_SYNTHETIC")
        self.assertEqual(r.candidate_pairs, ((0,1),))
        self.assertEqual(r.schema, SCHEMA)

    def test_disjoint_faces(self):
        v = [(0,0,0),(1,0,0),(0,1,0),(4,4,4),(5,4,4),(4,5,4)]
        r = audit_self_intersection_candidates(v, [(0,1,2),(3,4,5)], provenance="PUBLIC_SYNTHETIC")
        self.assertEqual(r.candidate_pairs, ())

    def test_inclusive_boundary_overlap(self):
        v = [(0,0,0),(1,0,0),(0,1,0),(1,1,0),(2,1,0),(1,2,0)]
        r = audit_self_intersection_candidates(v, [(0,1,2),(3,4,5)], provenance="PUBLIC_SYNTHETIC")
        self.assertEqual(r.candidate_pairs, ((0,1),))

    def test_shared_vertex_faces_skipped(self):
        v = [(0,0,0),(1,0,0),(0,1,0),(1,1,0)]
        r = audit_self_intersection_candidates(v, [(0,1,2),(1,3,2)], provenance="PUBLIC_SYNTHETIC")
        self.assertEqual((r.tested_pairs, r.candidate_pairs), (0, ()))

    def test_deterministic(self):
        v = [(0,0,0),(1,0,0),(0,1,0),(0,0,0),(1,0,0),(0,1,0)]
        f = [(0,1,2),(3,4,5)]
        self.assertEqual(audit_self_intersection_candidates(v,f,provenance="PUBLIC_SYNTHETIC"),
                         audit_self_intersection_candidates(v,f,provenance="PUBLIC_SYNTHETIC"))

    def test_rejects_private_provenance(self):
        with self.assertRaises(ValueError):
            audit_self_intersection_candidates([(0,0,0),(1,0,0),(0,1,0)],[(0,1,2)],provenance="PRIVATE")

    def test_rejects_bad_coordinates(self):
        for bad in (True, float("nan"), float("inf"), 10**10000):
            with self.subTest(kind=type(bad).__name__), self.assertRaises(ValueError):
                audit_self_intersection_candidates([(0,0,0),(1,0,0),(0,bad,0)],[(0,1,2)],provenance="PUBLIC_SYNTHETIC")

    def test_rejects_bad_or_duplicate_faces(self):
        v = [(0,0,0),(1,0,0),(0,1,0)]
        for f in ([(0,1,3)],[(0,1,2),(2,1,0)],[(0,0,1)]):
            with self.subTest(faces=f), self.assertRaises(ValueError):
                audit_self_intersection_candidates(v,f,provenance="PUBLIC_SYNTHETIC")

    def test_pair_budget_preflight(self):
        v = [(i,0,0) for i in range(12)]
        f = [(i,i+1,i+2) for i in range(10)]
        with self.assertRaises(ValueError):
            audit_self_intersection_candidates(v,f,provenance="PUBLIC_SYNTHETIC",max_pair_checks=1)

    def test_rejects_container_subclasses(self):
        class HostileList(list): pass
        with self.assertRaises(ValueError):
            audit_self_intersection_candidates(HostileList([(0,0,0),(1,0,0),(0,1,0)]),[(0,1,2)],provenance="PUBLIC_SYNTHETIC")


if __name__ == "__main__":
    unittest.main()
