import importlib.util
import json
import unittest
from unittest import mock
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "tools" / "tick_latency_histogram.py"
spec = importlib.util.spec_from_file_location("tick_latency_histogram", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def row(tick, duration, visibility="PUBLIC_SYNTHETIC"):
    return json.dumps({"tick": tick, "duration_us": duration, "visibility": visibility}) + "\n"


class HistogramTests(unittest.TestCase):
    def test_empty(self):
        result = mod.analyze([])
        self.assertEqual(result["count"], 0)
        self.assertIsNone(result["p95_us"])

    def test_nearest_rank_and_buckets(self):
        result = mod.analyze([row(i, i * 100) for i in range(1, 101)])
        self.assertEqual((result["p50_us"], result["p95_us"], result["p99_us"]), (5000, 9500, 9900))
        self.assertEqual(sum(b["count"] for b in result["buckets_us"]), 100)

    def test_reject_private_and_invalid(self):
        invalid = [
            [row(0, 1, "PRIVATE")],
            [row(0, -1)],
            [row(0, True)],
            [row(0, 1), row(0, 2)],
            [row(True, 1)],
            ['{"tick":0,"duration_us":NaN,"visibility":"PUBLIC_SYNTHETIC"}'],
            [row(0, 1).replace('"tick": 0', '"tick": 0, "extra": 1')],
            ["x" * 4097],
        ]
        for case in invalid:
            with self.subTest(case=case):
                with self.assertRaises(mod.TraceError):
                    mod.analyze(case)

    def test_reject_duplicate_json_keys(self):
        cases = [
            '{"tick":0,"duration_us":1,"visibility":"PRIVATE","visibility":"PUBLIC_SYNTHETIC"}',
            '{"tick":0,"tick":1,"duration_us":1,"visibility":"PUBLIC_SYNTHETIC"}',
            '{"tick":0,"duration_us":1,"duration_us":2,"visibility":"PUBLIC_SYNTHETIC"}',
        ]
        for source in cases:
            with self.subTest(source=source):
                with self.assertRaises(mod.TraceError):
                    mod.analyze([source])

    def test_reject_oversized_json_integer_without_overflow(self):
        source = '{"tick":0,"duration_us":' + "9" * 310 + ',"visibility":"PUBLIC_SYNTHETIC"}'
        with self.assertRaises(mod.TraceError):
            mod.analyze([source])

    def test_reject_invalid_unicode_from_direct_api(self):
        with self.assertRaises(mod.TraceError):
            mod.analyze(["\ud800"])

    def test_deeply_nested_json_is_bounded_rejection(self):
        # Fits the per-line byte budget; parser recursion ceilings vary by Python.
        deeply_nested = "[" * 1500 + "0" + "]" * 1500
        with self.assertRaises(mod.TraceError):
            mod.analyze([deeply_nested])

    def test_json_parser_recursion_error_is_normalized(self):
        with mock.patch.object(mod.json, "loads", side_effect=RecursionError("too deep")):
            with self.assertRaises(mod.TraceError) as caught:
                mod.analyze([row(0, 1)])
        self.assertIn("invalid JSON", str(caught.exception))

    def test_limits(self):
        with self.assertRaises(mod.TraceError):
            mod.analyze([row(i, 1) for i in range(mod.MAX_ROWS + 1)])


if __name__ == "__main__":
    unittest.main()
