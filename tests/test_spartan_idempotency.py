import unittest
from mojum.spartan_idempotency import provider_request_key

BASE = dict(issue=478, base_sha="a" * 40, patch_sha="b" * 40, provider="local-http", model="tiny.v1")


class IdempotencyTests(unittest.TestCase):
    def test_stable_and_bounded(self):
        key = provider_request_key(**BASE)
        self.assertEqual(key, provider_request_key(**BASE))
        self.assertEqual(len(key), 52)
        self.assertTrue(key.startswith("sp1_"))

    def test_distinct_inputs(self):
        original = provider_request_key(**BASE)
        for name, value in (("issue", 479), ("base_sha", "c" * 40), ("patch_sha", "d" * 40), ("provider", "other"), ("model", "other"), ("operation", "verify")):
            self.assertNotEqual(original, provider_request_key(**dict(BASE, **{name: value})))

    def test_fail_closed(self):
        for name, value in (("issue", True), ("issue", 0), ("issue", 2**31), ("base_sha", "A" * 40), ("patch_sha", "x" * 40), ("provider", "token secret"), ("model", "\n"), ("operation", "")):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                provider_request_key(**dict(BASE, **{name: value}))


if __name__ == "__main__":
    unittest.main()
