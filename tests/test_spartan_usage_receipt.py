import unittest
from dataclasses import replace
from mojum.spartan_usage_receipt import make_usage_receipt, within_budget

class UsageReceiptTests(unittest.TestCase):
    def test_deterministic_budget(self):
        kw = dict(request_id="issue478:1", provider="local", model="small-v1", input_tokens=5, output_tokens=8, usd="0.000125")
        a = make_usage_receipt(**kw)
        self.assertEqual(a, make_usage_receipt(**kw))
        self.assertEqual(a.usd_micros, 125)
        self.assertTrue(within_budget((a, a), limit_usd="0.000250"))
        self.assertFalse(within_budget((a, a), limit_usd="0.000249"))

    def test_fail_closed(self):
        kw = dict(request_id="r1", provider="p", model="m", input_tokens=1, output_tokens=2, usd="0.1")
        for key, value in (("request_id", "token secret"), ("provider", "../../x"),
                           ("input_tokens", True), ("output_tokens", -1),
                           ("usd", "1e2"), ("usd", "NaN"), ("usd", "0.0000001")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                make_usage_receipt(**(kw | {key: value}))
        a = make_usage_receipt(**kw)
        with self.assertRaises(ValueError):
            within_budget((replace(a, usd_micros=0),), limit_usd="1")
        with self.assertRaises(ValueError):
            within_budget([a], limit_usd="1")

    def test_no_sensitive_fields(self):
        a = make_usage_receipt(request_id="r", provider="p", model="m", input_tokens=0, output_tokens=0, usd="0")
        self.assertEqual(set(a.__dataclass_fields__), {"request_id", "provider", "model", "input_tokens", "output_tokens", "usd_micros", "fingerprint"})

if __name__ == "__main__":
    unittest.main()
