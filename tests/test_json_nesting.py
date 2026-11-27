"""Regression coverage for the shared pre-decoder container depth guard."""
import json
import unittest

from mojum_core._json_nesting import MAX_JSON_DEPTH, exceeds_json_depth


class JsonNestingTests(unittest.TestCase):
    def test_default_boundary(self):
        for depth in (255, 256, 257, 10000):
            with self.subTest(depth=depth):
                text = '[' * depth + '0' + ']' * depth
                self.assertEqual(exceeds_json_depth(text), depth > 256)
        self.assertEqual(MAX_JSON_DEPTH, 256)

    def test_quoted_delimiters_and_escape_parity(self):
        for count in range(8):
            key = '\\' * count + '"[{]}' * 40
            value = '\\' * count + '"[{]}' * 40 + '\\' * count
            for ascii_only in (True, False):
                text = json.dumps({key: [value + 'é']}, ensure_ascii=ascii_only)
                with self.subTest(count=count, ascii_only=ascii_only):
                    self.assertFalse(exceeds_json_depth(text, 2))
                    self.assertTrue(exceeds_json_depth(text, 1))

    def test_siblings_do_not_accumulate_depth(self):
        text = json.dumps([{'a': [0]} for _ in range(1000)])
        self.assertFalse(exceeds_json_depth(text, 3))
        self.assertTrue(exceeds_json_depth(text, 2))

    def test_scalar_and_mixed_container_boundaries(self):
        self.assertFalse(exceeds_json_depth(json.dumps('[[{{"'), 0))
        self.assertFalse(exceeds_json_depth('null', 0))
        self.assertTrue(exceeds_json_depth('{}', 0))
        text = '{"k":[' * 128 + 'null' + ']}' * 128
        self.assertFalse(exceeds_json_depth(text))
        self.assertTrue(exceeds_json_depth('[' + text + ']'))
