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


    def test_malformed_node_ids_rejected(self):
        for node_id in (" ", "\x00", "\n", "test\nnode", "👻",
                        "../node", "foo/bar", "..", ".node", "node.",
                        "-node", "node--", "node::other"):
            with self.subTest(node_id=node_id), self.assertRaises(ValueError):
                negotiate(self.a, Offer(PROTOCOL, node_id, ("cpu",)),
                          authenticated=True, enrolled=True)

    def test_ambiguous_capability_segments_rejected(self):
        for cap in ("..", "../secret", "/", "cpu//gpu", "cpu..gpu",
                    "physics.mujoco/", "-", "__", "cpu--gpu",
                    "cpu._.gpu", "cpu\x00gpu", ".hidden"):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                negotiate(Offer(PROTOCOL, "local", (cap,)),
                          Offer(PROTOCOL, "remote", (cap,)),
                          authenticated=True, enrolled=True)

    def test_normal_capability_paths_and_ids_still_accepted(self):
        ours = Offer(PROTOCOL, "node-01", ("cpu", "physics.mujoco", "net/tcp", "gpu_cuda"))
        theirs = Offer(PROTOCOL, "peer:02", ("net/tcp", "gpu_cuda", "cpu"))
        self.assertEqual(negotiate(ours, theirs, authenticated=True, enrolled=True),
                         ("cpu", "gpu_cuda", "net/tcp"))

    def test_overlong_identity_and_capability_rejected(self):
        with self.assertRaises(ValueError):
            negotiate(self.a, Offer(PROTOCOL, "x" * 129, ("cpu",)),
                      authenticated=True, enrolled=True)
        with self.assertRaises(ValueError):
            negotiate(self.a, Offer(PROTOCOL, "peer", ("x" * 129,)),
                      authenticated=True, enrolled=True)

    def test_trust_gate_kept_fail_closed(self):
        remote = Offer(PROTOCOL, "peer-01", ("cpu",))
        for authenticated, enrolled in ((False, False), (True, False), (False, True)):
            with self.assertRaises(PermissionError):
                negotiate(self.a, remote, authenticated=authenticated, enrolled=enrolled)

    def test_no_runtime_enrollment_in_contract(self):
        import mojum.mesh_contract as contract
        for name in ("enroll", "discover", "authorize", "grant_capability", "connect"):
            self.assertFalse(hasattr(contract, name))

if __name__ == "__main__":
    unittest.main()

