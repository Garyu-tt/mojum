"""Capped exact placement checked against independent exhaustive set packing."""
import contextlib
import io
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import gpu_legion_plan as gpu


def inventory(usable, indices=None):
    indices = range(len(usable)) if indices is None else indices
    return {"source": "synthetic", "gpus": [
        {"index": index, "name": "synthetic GPU", "total_mib": amount + 256,
         "free_mib": amount + 256, "compute_cap": "8.0"}
        for index, amount in zip(indices, usable)
    ]}


def plan(data, cap, max_gpus=4):
    return gpu.plan(data, gpu.validate_inventory(data), model_mib=4096,
                    reserve_mib=256, max_replicas=cap, max_gpus=max_gpus)


def groups(report):
    return tuple(tuple(replica["gpu_indices"]) for replica in report["replicas"])


def exhaustive_oracle(usable, cap, max_gpus):
    """Enumerate sets of feasible groups, without production mask recursion."""
    candidates = [group for count in range(1, max_gpus + 1)
                  for group in itertools.combinations(range(len(usable)), count)
                  if sum(usable[i] for i in group) >= 4096]
    layouts = [()]
    for count in range(1, min(cap, len(usable)) + 1):
        for layout in itertools.combinations(candidates, count):
            flat = [i for group in layout for i in group]
            if len(flat) == len(set(flat)):
                layouts.append(tuple(sorted(layout)))
    return min(layouts, key=lambda layout: (
        -len(layout), sum(map(len, layout)),
        sum(sum(usable[i] for i in group) - 4096 for group in layout), layout))


class CappedPlacementTests(unittest.TestCase):
    def test_cap_chooses_single_card_instead_of_truncated_two_card_group(self):
        data = inventory([2048, 2048, 4096])
        report = plan(data, 1)
        self.assertEqual(groups(report), ((2,),))
        self.assertEqual(report["unused_gpu_indices"], [0, 1])
        self.assertEqual(report["replicas"][0]["env"]["CUDA_VISIBLE_DEVICES"], "2")
        self.assertEqual(groups(plan(data, 2)), ((0, 1), (2,)))

    def test_cap_minimizes_slack_before_index_tie_break(self):
        self.assertEqual(groups(plan(inventory([8192, 4096, 4096]), 1)), ((1,),))
        self.assertEqual(groups(plan(inventory([8192, 4096, 4096]), 2)), ((1,), (2,)))

    def test_every_four_card_inventory_matches_exhaustive_oracle(self):
        # 81 inventories x 4 group limits x 4 replica caps = 1296 plans.
        for usable in itertools.product((0, 2048, 4096), repeat=4):
            data = inventory(usable)
            for max_gpus in range(1, 5):
                for cap in range(1, 5):
                    with self.subTest(usable=usable, max_gpus=max_gpus, cap=cap):
                        self.assertEqual(groups(plan(data, cap, max_gpus)),
                                         exhaustive_oracle(usable, cap, max_gpus))

    def test_sparse_indices_and_inventory_order_do_not_change_optimum(self):
        data = inventory([2048, 2048, 4096], [3, 17, 240])
        expected = plan(data, 1)
        self.assertEqual(groups(expected), ((240,),))
        for ordering in itertools.permutations(data["gpus"]):
            self.assertEqual(plan({**data, "gpus": list(ordering)}, 1), expected)

    def test_public_cap_bounds_remain_strict(self):
        data = inventory([4096])
        for cap in (0, -1, 33, None, True, 1.0):
            with self.subTest(cap=cap), self.assertRaises(gpu.InventoryError):
                plan(data, cap)
        self.assertEqual(groups(plan(data, 32)), ((0,),))

    def test_twelve_card_bound_and_large_fleet_heuristic(self):
        for cap in (1, 6, 12, 32):
            report = plan(inventory([4096] * 12), cap)
            self.assertEqual(groups(report), tuple((i,) for i in range(min(cap, 12))))
            self.assertEqual(report["placement_strategy"], "exact_max_replicas")
        report = plan(inventory([4096] * 13), 1)
        self.assertEqual(groups(report), ((0,),))
        self.assertEqual(report["placement_strategy"], "bounded_greedy")

    def test_cli_cap_and_required_replica_exit_status(self):
        data = inventory([2048, 2048, 4096])
        for required, status in ((1, 0), (2, 2)):
            output = io.StringIO()
            with patch.object(gpu, "probe_nvidia_smi",
                              return_value=(data, gpu.validate_inventory(data))), \
                    contextlib.redirect_stdout(output):
                code = gpu.main(["--model-mib", "4096", "--reserve-mib", "256",
                                 "--max-replicas", "1", "--require-replicas", str(required)])
            self.assertEqual(code, status)
            report = json.loads(output.getvalue())
            self.assertEqual(groups(report), ((2,),))
            self.assertEqual(report["shortfall"], required - 1)
            self.assertFalse(report["deploy_performed"])

    @unittest.skipUnless(os.name == "posix", "secure inventory reads require POSIX")
    def test_real_cli_accepts_inventory_and_preserves_offline_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps(inventory([2048, 2048, 4096])), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", str(ROOT / "tools/gpu_legion_plan.py"),
                 "--inventory", str(path), "--model-mib", "4096", "--reserve-mib", "256",
                 "--max-replicas", "1", "--require-replicas", "1"],
                capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(groups(report), ((2,),))
            self.assertFalse(report["observed_local_inventory"])
            self.assertFalse(report["hardware_verified"])


if __name__ == "__main__":
    unittest.main()
