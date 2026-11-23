"""Hermetic source-disjoint PUBLIC dataset split regressions."""
import copy
import unittest

from mojum.dataset_view import project_episodes
from mojum.dataset_split import (
    MAX_RECORDS, SCHEMA, split_public_view, verify_public_split,
)


def public_view(records):
    return project_episodes(
        [
            {"id": i, "source": src, "tick": tick,
             "observation": obs, "visibility": "PUBLIC"}
            for i, src, tick, obs in records
        ],
        allowed_sources={item[1] for item in records},
        max_records=len(records),
    )


class DatasetSplitTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            ("id-1", "camera", 1, "synthetic pixel"),
            ("id-2", "camera", 3, "synthetic pixel"),
            ("id-3", "touch", 2, "synthetic force"),
            ("id-4", "audio", 4, "synthetic wave"),
            ("id-5", "inertial", 5, "synthetic motion"),
            ("id-6", "proprioception", 6, "synthetic joint"),
        ]
        self.view = public_view(self.records)

    def test_deterministic_and_verifiable(self):
        a = split_public_view(self.view)
        b = split_public_view(self.view)
        self.assertEqual(a, b)
        self.assertEqual(a["schema"], SCHEMA)
        self.assertTrue(verify_public_split(self.view, a))
        self.assertEqual(len(a["manifest_sha256"]), 64)

    def test_source_disjoint_and_complete(self):
        out = split_public_view(self.view)
        membership = {}
        collected = []
        for part in ("train", "validation", "test"):
            for row in out[part]["records"]:
                collected.append(row["id"])
                old = membership.setdefault(row["source"], part)
                self.assertEqual(old, part)
        self.assertEqual(sorted(collected), sorted(r["id"] for r in self.view["records"]))
        self.assertEqual(len(collected), len(set(collected)))

    def test_source_stability_after_append(self):
        first = split_public_view(self.view, seed="stable")
        view2 = public_view(self.records + [
            ("id-7", "camera", 7, "another synthetic pixel"),
            ("id-8", "audio", 8, "another synthetic wave"),
        ])
        second = split_public_view(view2, seed="stable")
        where = lambda output, src: next(
            part for part in ("train", "validation", "test")
            if any(r["source"] == src for r in output[part]["records"])
        )
        for src in {record[1] for record in self.records}:
            self.assertEqual(where(first, src), where(second, src))

    def test_reordered_input_results_same_view_and_split(self):
        reverse = public_view(list(reversed(self.records)))
        self.assertEqual(self.view, reverse)
        self.assertEqual(split_public_view(self.view), split_public_view(reverse))

    def test_explicit_zero_weight_partitions(self):
        out = split_public_view(self.view, train=100, validation=0, test=0)
        self.assertEqual(out["train"]["records"], self.view["records"])
        self.assertEqual(out["validation"]["records"], [])
        self.assertEqual(out["test"]["records"], [])
        self.assertTrue(verify_public_split(self.view, out))

    def test_empty_view(self):
        view = public_view([])
        out = split_public_view(view)
        self.assertTrue(verify_public_split(view, out))
        self.assertFalse(any(out[p]["records"] for p in ("train", "validation", "test")))

    def test_small_single_source_not_artificially_balanced(self):
        view = public_view([
            ("a", "one", 0, "x"), ("b", "one", 1, "y")
        ])
        out = split_public_view(view)
        sizes = sorted(len(out[p]["records"]) for p in ("train", "validation", "test"))
        self.assertEqual(sizes, [0, 0, 2])

    def test_reject_tampered_source_view_digest(self):
        mutated = copy.deepcopy(self.view)
        mutated["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            split_public_view(mutated)

    def test_reject_noncanonical_order(self):
        mutated = copy.deepcopy(self.view)
        mutated["records"].reverse()
        with self.assertRaises(ValueError):
            split_public_view(mutated)

    def test_reject_mutated_observation_and_raw_privacy_fields(self):
        mutated = copy.deepcopy(self.view)
        mutated["records"][0]["observation"] = "forged"
        with self.assertRaises(ValueError):
            split_public_view(mutated)
        mutated = copy.deepcopy(self.view)
        mutated["records"][0]["visibility"] = "PRIVATE"
        with self.assertRaises(ValueError):
            split_public_view(mutated)

    def test_reject_forged_split_even_with_recomputed_header(self):
        out = split_public_view(self.view)
        forged = copy.deepcopy(out)
        first = next(p for p in ("train", "validation", "test") if forged[p]["records"])
        forged[first]["records"][0]["tick"] += 100
        with self.assertRaises(ValueError):
            verify_public_split(self.view, forged)

    def test_reject_unknown_manifest_fields(self):
        out = split_public_view(self.view)
        out["network_policy"] = "enabled"
        with self.assertRaises(ValueError):
            verify_public_split(self.view, out)

    def test_invalid_percentages_and_bool_weights(self):
        for kwargs in (
            {"train": 80, "validation": 20, "test": 10},
            {"train": True, "validation": 0, "test": 99},
            {"train": 80.0, "validation": 10, "test": 10},
            {"train": -1, "validation": 1, "test": 100},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                split_public_view(self.view, **kwargs)

    def test_invalid_seed(self):
        for seed in ("", "x" * 257, "\ud800", None):
            with self.subTest(seed=repr(seed)), self.assertRaises(ValueError):
                split_public_view(self.view, seed=seed)

    def test_invalid_records_and_bounds(self):
        for value in (None, (), [None], [{}], [{"source": []}], [1] * (MAX_RECORDS + 1)):
            mutated = copy.deepcopy(self.view)
            mutated["records"] = value
            with self.subTest(value=str(value)[:40]), self.assertRaises(ValueError):
                split_public_view(mutated)

    def test_returns_detached_records(self):
        out = split_public_view(self.view)
        rec = next(r for p in ("train", "validation", "test")
                   for r in out[p]["records"] if r["id"] == "id-1")
        rec["observation"] = "mutated result"
        self.assertEqual(
            next(r["observation"] for r in self.view["records"] if r["id"] == "id-1"),
            "synthetic pixel",
        )
        with self.assertRaises(ValueError):
            verify_public_split(self.view, out)

    def test_same_seed_different_weight_reassigns_but_is_verifiable(self):
        a = split_public_view(self.view, train=100, validation=0, test=0)
        b = split_public_view(self.view, train=0, validation=0, test=100)
        self.assertNotEqual(a["manifest_sha256"], b["manifest_sha256"])
        self.assertTrue(verify_public_split(self.view, a))
        self.assertTrue(verify_public_split(self.view, b))


if __name__ == "__main__":
    unittest.main()
