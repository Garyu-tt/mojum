import unittest
from mojum.mesh_contract import Offer, PROTOCOL, negotiate


class MeshContractTests(unittest.TestCase):
    def setUp(self):
        self.a = Offer(PROTOCOL, "a", ("cpu", "physics.mujoco", "memory.read"))
        self.b = Offer(PROTOCOL, "b", ("cpu", "world.room"))

    def test_untrusted_denied(self):
        for a, b in ((False, False), (True, False), (False, True)):
            with self.subTest(a=a, b=b), self.assertRaises(PermissionError):
                negotiate(self.a, self.b, authenticated=a, enrolled=b)

    def test_intersection_is_sorted(self):
        self.assertEqual(negotiate(self.a, self.b, authenticated=True, enrolled=True), ("cpu",))

    def test_protocol_mismatch(self):
        with self.assertRaises(ValueError):
            negotiate(self.a, Offer("future/2", "b", ("cpu",)), authenticated=True, enrolled=True)

    def test_same_identity(self):
        with self.assertRaises(ValueError):
            negotiate(self.a, Offer(PROTOCOL, "a", ("cpu",)), authenticated=True, enrolled=True)

    def test_malformed_and_duplicate_capabilities(self):
        for caps in (("cpu", "cpu"), ("CPU",), ("",), ("cpu space",), ("cpu", 2)):
            with self.subTest(caps=caps), self.assertRaises(ValueError):
                negotiate(self.a, Offer(PROTOCOL, "b", caps), authenticated=True, enrolled=True)

    def test_no_capability_overlap(self):
        self.assertEqual(negotiate(self.a, Offer(PROTOCOL, "b", ("gpu",)), authenticated=True, enrolled=True), ())

    def test_non_boolean_trust_denied(self):
        with self.assertRaises(ValueError):
            negotiate(self.a, self.b, authenticated=1, enrolled=True)


if __name__ == "__main__":
    unittest.main()
