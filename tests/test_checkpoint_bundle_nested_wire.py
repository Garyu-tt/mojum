"""Adversarial JSON parsing must honor the checkpoint API's ValueError contract."""
import unittest

from mojum.checkpoint_bundle import loads


class CheckpointBundleNestedWireTests(unittest.TestCase):
    def test_nested_arrays_fail_closed_without_raw_recursion_error(self):
        # CPython's decoder raises RecursionError well below the 30 MB wire cap.
        deeply_nested = "[" * 10000 + "0" + "]" * 10000
        self.assertLess(len(deeply_nested), 30_000_000)
        with self.assertRaises(ValueError):
            loads(deeply_nested)

    def test_nested_objects_fail_closed_without_raw_recursion_error(self):
        deeply_nested = '{"x":' * 10000 + "0" + "}" * 10000
        self.assertLess(len(deeply_nested), 30_000_000)
        with self.assertRaises(ValueError):
            loads(deeply_nested)


if __name__ == "__main__":
    unittest.main()
