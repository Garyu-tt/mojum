"""GPU Legion planner tests: no model or real GPU required."""
import contextlib
import io
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

# The 3.13 leak audit discovers tests with tests/ as sys.path[0].
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import gpu_legion_plan as gpu


def p106_inventory(count=7):
    data = {"source": "synthetic", "gpus": [
        {"index": i, "name": "P106-100 synthetic", "total_mib": 6144,
         "free_mib": 6144, "compute_cap": "6.1"} for i in range(count)
    ]}
    return data, gpu.validate_inventory(data)


class GpuLegionPlanTests(unittest.TestCase):
    def test_seven_six_gig_gpus_yield_two_three_card_replicas(self):
        data, devices = p106_inventory()
        report = gpu.plan(data, devices)
        self.assertEqual(report["estimated_replicas"], 2)
        self.assertEqual(report["live_replicas"], 0)
        self.assertFalse(report["deploy_performed"])
        self.assertFalse(report["hardware_verified"])
        self.assertFalse(report["observed_local_inventory"])
        self.assertEqual([x["gpu_indices"] for x in report["replicas"]],
                         [[0, 1, 2], [3, 4, 5]])
        self.assertEqual(report["unused_gpu_indices"], [6])
        self.assertEqual(report["replicas"][0]["env"]["CUDA_VISIBLE_DEVICES"], "0,1,2")
        self.assertEqual(report["replicas"][0]["argv"][0], "llama-server")
        self.assertIn(":18180/", report["replicas"][0]["endpoint"])
        self.assertNotIn(":18080/", report["replicas"][0]["endpoint"])
        self.assertIn("--jinja", report["replicas"][0]["argv"])
        self.assertIn("Pascal", " ".join(report["warnings"]))

    def test_heterogeneous_fleet_maximizes_real_replicas(self):
        # Greedy pairing 9+9 GiB strands four 3 GiB devices, reporting one.
        # Two 9+3+3 GiB groups both fit and use disjoint GPU indices.
        usable = [3072, 3072, 3072, 3072, 9216, 9216]
        data = {"source": "synthetic", "gpus": [
            {"index": i, "name": "mixed synthetic GPU", "total_mib": 12288,
             "free_mib": amount + 256, "compute_cap": "8.6"}
            for i, amount in enumerate(usable)
        ]}
        report = gpu.plan(data, gpu.validate_inventory(data),
                          model_mib=15360, reserve_mib=256, max_gpus=3)
        self.assertEqual(report["placement_strategy"], "exact_max_replicas")
        self.assertEqual(report["estimated_replicas"], 2)
        self.assertEqual([r["gpu_indices"] for r in report["replicas"]],
                         [[0, 1, 4], [2, 3, 5]])
        self.assertEqual(report["unused_gpu_indices"], [])
        self.assertEqual(report["live_replicas"], 0)

    def test_large_fleet_is_labeled_nonoptimal_heuristic(self):
        data, devices = p106_inventory(13)
        report = gpu.plan(data, devices)
        self.assertEqual(report["placement_strategy"], "bounded_greedy")
        self.assertEqual(report["estimated_replicas"], 4)
        self.assertTrue(any("undercount" in warning for warning in report["warnings"]))

    def test_no_claim_for_two_small_gpus(self):
        data, devices = p106_inventory(2)
        self.assertEqual(gpu.plan(data, devices)["estimated_replicas"], 0)

    def test_single_large_gpu_gets_its_own_instance(self):
        data = {"source": "nvidia-smi", "gpus": [
            {"index": 2, "name": "fast card", "total_mib": 24576,
             "free_mib": 23000, "compute_cap": "8.6"}
        ]}
        report = gpu.plan(data, gpu.validate_inventory(data))
        self.assertEqual(report["replicas"][0]["gpu_indices"], [2])
        self.assertFalse(report["hardware_verified"])
        self.assertFalse(report["observed_local_inventory"])
        self.assertEqual(report["live_replicas"], 0)

    def test_replicas_never_share_card_and_ports_unique(self):
        data, devices = p106_inventory(12)
        report = gpu.plan(data, devices, max_replicas=3, base_port=19100)
        ids = [idx for replica in report["replicas"] for idx in replica["gpu_indices"]]
        ports = [replica["endpoint"] for replica in report["replicas"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ports), len(set(ports)))
        self.assertEqual(len(report["replicas"]), 3)

    def test_reject_invalid_mem_indexes_compute_caps(self):
        data, _ = p106_inventory(1)
        cases = [
            {"index": True}, {"index": -1}, {"total_mib": 0},
            {"free_mib": 10000}, {"compute_cap": "1; rm -rf /"},
            {"free_mib": 1.5},
        ]
        for change in cases:
            with self.subTest(change=change):
                edited = {"source": "synthetic", "gpus": [{**data["gpus"][0], **change}]}
                with self.assertRaises(gpu.InventoryError):
                    gpu.validate_inventory(edited)

    def test_duplicate_index_and_invalid_source(self):
        data, _ = p106_inventory(2)
        data["gpus"][1]["index"] = data["gpus"][0]["index"]
        with self.assertRaisesRegex(gpu.InventoryError, "duplicated"):
            gpu.validate_inventory(data)
        with self.assertRaises(gpu.InventoryError):
            gpu.validate_inventory({"source": "trust-me", "gpus": []})

    def test_unsupported_old_device_is_never_placed(self):
        data, _ = p106_inventory(3)
        data["gpus"][0]["compute_cap"] = "5.2"
        report = gpu.plan(data, gpu.validate_inventory(data))
        self.assertEqual(report["estimated_replicas"], 0)

    def test_fails_on_bad_planning_parameters(self):
        data, devices = p106_inventory()
        cases = [{"model_mib": 0}, {"reserve_mib": -1},
                 {"max_gpus": 0}, {"max_gpus": 5},
                 {"base_port": 65535}, {"ctx_size": 1}]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(gpu.InventoryError):
                gpu.plan(data, devices, **kwargs)

    def test_load_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "gpus.json"
            file.write_text('{"source":"synthetic","source":"nvidia-smi","gpus":[]}')
            with self.assertRaisesRegex(gpu.InventoryError, "duplicate JSON key"):
                gpu.load_inventory(file)

    def test_load_rejects_oversized_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "gpus.json"
            file.write_bytes(b" " * (gpu.MAX_INPUT_BYTES + 1))
            with self.assertRaisesRegex(gpu.InventoryError, "exceeds"):
                gpu.load_inventory(file)


    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NONBLOCK"),
                         "POSIX nonblocking descriptor required")
    def test_fifo_snapshot_is_rejected_without_waiting_for_writer(self):
        # An accidental regression removing O_NONBLOCK must fail the test,
        # not strand the entire CI runner on a FIFO open with no writer.
        with tempfile.TemporaryDirectory() as folder:
            fifo = Path(folder) / "blocked.json"
            os.mkfifo(fifo)
            script = (
                "import sys\n"
                "from tools import gpu_legion_plan as gpu\n"
                "try:\n"
                "    gpu.load_inventory(sys.argv[1])\n"
                "except gpu.InventoryError as exc:\n"
                "    if 'regular file' in str(exc):\n"
                "        raise SystemExit(0)\n"
                "    raise\n"
                "raise SystemExit('FIFO unexpectedly accepted')\n"
            )
            completed = subprocess.run(
                [sys.executable, "-c", script, str(fifo)],
                cwd=REPO_ROOT, capture_output=True, text=True,
                timeout=5, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"),
                         "POSIX nofollow descriptor required")
    def test_symlink_snapshot_is_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as folder:
            real = Path(folder) / "private.json"
            real.write_text('{"source":"synthetic","gpus":[]}')
            alias = Path(folder) / "alias.json"
            alias.symlink_to(real)
            with self.assertRaises(OSError):
                gpu.load_inventory(alias)

    def test_sparse_huge_file_rejected_before_materializing_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot = Path(folder) / "sparse.json"
            with snapshot.open("wb") as output:
                output.truncate(1024**3)
            with self.assertRaisesRegex(gpu.InventoryError, "exceeds 64 KiB"):
                gpu.load_inventory(snapshot)

    @unittest.skipUnless(
        all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC")),
        "secure local inventory reads required",
    )
    def test_exact_maximum_inventory_size_remains_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot = Path(folder) / "exact.json"
            payload = b'{"source":"synthetic","gpus":[]}'
            snapshot.write_bytes(payload + b" " * (gpu.MAX_INPUT_BYTES - len(payload)))
            data, devices = gpu.load_inventory(snapshot)
            self.assertEqual(data["source"], "synthetic")
            self.assertEqual(devices, [])

    def test_imported_json_cannot_claim_hardware_verification(self):
        data, _ = p106_inventory(3)
        data["source"] = "nvidia-smi"  # Attacker-controlled label
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "forged.json"
            file.write_text(json.dumps(data))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = gpu.main(["--inventory", str(file), "--require-replicas", "1"])
        self.assertEqual(rc, 0)
        report = json.loads(output.getvalue())
        self.assertFalse(report["hardware_verified"])
        self.assertFalse(report["observed_local_inventory"])
        self.assertEqual(report["live_replicas"], 0)

    def test_actual_probe_marks_observation_not_attestation(self):
        data, devices = p106_inventory(3)
        data["source"] = "nvidia-smi"
        output = io.StringIO()
        with patch.object(gpu, "probe_nvidia_smi", return_value=(data, devices)):
            with contextlib.redirect_stdout(output):
                rc = gpu.main(["--require-replicas", "1"])
        self.assertEqual(rc, 0)
        report = json.loads(output.getvalue())
        self.assertFalse(report["hardware_verified"])
        self.assertTrue(report["observed_local_inventory"])
        self.assertFalse(report["deploy_performed"])
        self.assertEqual(report["replicas"][0]["endpoint"],
                         "http://127.0.0.1:18180/v1/chat/completions")

    def test_nvidia_smi_csv_probe_mock(self):
        class Completed:
            stdout = "0, NVIDIA P106-100, 6144, 6070, 6.1\n"
        with patch.object(gpu.subprocess, "run", return_value=Completed()) as run:
            data, devices = gpu.probe_nvidia_smi()
        self.assertEqual(data["source"], "nvidia-smi")
        self.assertEqual(devices[0]["free_mib"], 6070)
        self.assertEqual(run.call_args.args[0][0], "nvidia-smi")

    def test_missing_nvidia_smi_fails_closed(self):
        with patch.object(gpu.subprocess, "run", side_effect=FileNotFoundError()):
            with self.assertRaises(gpu.InventoryError):
                gpu.probe_nvidia_smi()

    def test_cli_require_two_from_sample_and_shortfall_three(self):
        data, _ = p106_inventory()
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "gpus.json"
            file.write_text(json.dumps(data))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = gpu.main(["--inventory", str(file), "--require-replicas", "2"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(output.getvalue())["estimated_replicas"], 2)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = gpu.main(["--inventory", str(file), "--require-replicas", "3"])
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(output.getvalue())["shortfall"], 1)


if __name__ == "__main__":
    unittest.main()
