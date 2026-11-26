import unittest
from mojum.spartan_rate_window import RequestEvent, admit_request


class RateWindowTests(unittest.TestCase):
    def test_admission_and_expiration(self):
        e = (RequestEvent(1000, 4), RequestEvent(2000, 5))
        self.assertEqual(admit_request(e, now_ms=2000, new_tokens=1,
                                       max_requests=2, max_tokens=10), (False, 61000))
        self.assertEqual(admit_request(e, now_ms=61000, new_tokens=6,
                                       max_requests=2, max_tokens=10), (False, 62000))
        self.assertEqual(admit_request(e, now_ms=62000, new_tokens=6,
                                       max_requests=2, max_tokens=10), (True, 62000))

    def test_token_pressure(self):
        e = (RequestEvent(100, 7), RequestEvent(200, 2))
        self.assertEqual(admit_request(e, now_ms=200, new_tokens=4,
                                       max_requests=3, max_tokens=10), (False, 60100))

    def test_impossible_request_is_rejected_without_false_retry(self):
        with self.assertRaisesRegex(ValueError, "exceeds rate window token limit"):
            admit_request((), now_ms=0, new_tokens=11,
                          max_requests=3, max_tokens=10)

    def test_reject_hostile_types_and_order(self):
        base = dict(events=(), now_ms=10, new_tokens=1, max_requests=2, max_tokens=10)
        for patch in ({'now_ms': True}, {'new_tokens': 0}, {'max_requests': False},
                      {'events': []}, {'events': (RequestEvent(11, 1),)},
                      {'events': (RequestEvent(9, 1), RequestEvent(8, 1))},
                      {'events': (RequestEvent(9, True),)}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                admit_request(**(base | patch))


if __name__ == '__main__':
    unittest.main()
