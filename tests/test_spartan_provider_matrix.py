"""Offline evidence for #696: no provider calls, no secrets, no scheduler writes."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import spartan_provider_matrix as pm
from types import SimpleNamespace


def fixture(**overrides):
    model = dict(id="cpu-7b", transport="llama.cpp", model="QwenCoder7B",
                 model_sha256="a" * 64, runtime_version="b7010",
                 endpoint="http://127.0.0.1:18182", device="cpu",
                 ram_mib=8192, vram_mib=0, slots_total=4, slots_free=4,
                 context_tokens=4096, decode_tokens_per_sec=512,
                 micro_usd_per_1k_tokens=0)
    model.update(overrides)
    return {"schema": pm.SCHEMA, "evidence": "synthetic-fixture", "models": [model]}


def parse(data):
    return pm.parse_snapshot(json.dumps(data).encode())


def run(snap, **kw):
    args = dict(ram_budget_mib=16384, vram_budget_mib=0,
                token_budget=1_000_000, micro_usd_budget=0,
                prompt_tokens=256, completion_tokens=256,
                window_seconds=10)
    args.update(kw)
    return pm.matrix(snap, **args)


class ProviderMatrixTests(unittest.TestCase):
    def test_burst_matrix_is_not_300_live_models(self):
        result = run(parse(fixture()), worker_leases=300)
        self.assertTrue(result["simulation_only"])
        self.assertEqual(result["resident_models"], 1)
        self.assertEqual(result["worker_leases"], 300)
        self.assertEqual([r["admitted"] for r in result["bursts"]], [1, 2, 4, 4])
        self.assertEqual(result["bursts"][3]["reasons"],
                         {"slots_or_decode_capacity_exhausted": 4})

    def test_worker_lease_limit_admits_partial_burst(self):
        out = run(parse(fixture()), counts=(4,), worker_leases=2)
        burst = out["bursts"][0]
        self.assertEqual(burst["admitted"], 2)
        self.assertEqual(burst["denied"], 2)
        self.assertEqual(burst["reasons"], {"worker_lease_bound": 2})

    def test_memory_counts_resident_even_when_slot_busy(self):
        obj = fixture(slots_free=0, ram_mib=8192)
        self.assertEqual(run(parse(obj), ram_budget_mib=8000)["bursts"][0]["reasons"],
                         {"resident_memory_over_budget": 1})

    def test_gpu_vram_and_ram_checked_together(self):
        obj = fixture(device="gpu", vram_mib=6144)
        self.assertEqual(run(parse(obj), vram_budget_mib=4096)["bursts"][0]["admitted"], 0)
        self.assertEqual(run(parse(obj), vram_budget_mib=8192)["bursts"][2]["admitted"], 4)

    def test_context_fails_closed(self):
        result = run(parse(fixture(context_tokens=128)), prompt_tokens=1024)
        self.assertEqual(result["bursts"][0]["reasons"], {"context_exceeded": 1})

    def test_decode_budget_tightly_bounds_burst(self):
        result = run(parse(fixture(decode_tokens_per_sec=10)),
                     window_seconds=20, completion_tokens=100)
        self.assertEqual([r["admitted"] for r in result["bursts"]], [1, 2, 2, 2])

    def test_token_and_usd_reservations_include_retries(self):
        obj = fixture(micro_usd_per_1k_tokens=1000)
        out = run(parse(obj), counts=(1, 2, 4), max_retries=1,
                  micro_usd_budget=2048, token_budget=2048)
        self.assertEqual([r["admitted"] for r in out["bursts"]], [1, 2, 2])
        self.assertEqual(out["bursts"][-1]["reserved_tokens"], 2048)
        self.assertEqual(out["bursts"][-1]["reserved_microusd"], 2048)

    def test_no_premature_token_or_usd_refund(self):
        obj = fixture(micro_usd_per_1k_tokens=1000)
        out = run(parse(obj), counts=(4,), micro_usd_budget=1024)
        self.assertEqual(out["bursts"][0]["admitted"], 2)
        self.assertEqual(out["bursts"][0]["reasons"], {"usd_reservation_exhausted": 2})

    def test_stable_model_selection_prefer_local_free(self):
        obj = fixture()
        model2 = dict(obj["models"][0], id="gpu-paid", transport="litellm",
                      endpoint="http://127.0.0.1:18181", device="gpu",
                      vram_mib=2048, ram_mib=1000,
                      micro_usd_per_1k_tokens=5000)
        obj["models"].append(model2)
        out = run(parse(obj), counts=(8,), vram_budget_mib=4096,
                  micro_usd_budget=99999)
        self.assertEqual(out["bursts"][0]["placements"], {"cpu-7b": 4, "gpu-paid": 4})
        self.assertEqual(out["bursts"][0]["admitted"], 8)

    def test_live_llama_slots_cannot_be_forged(self):
        snap = parse(fixture())
        backend = SimpleNamespace(port=18182, model="QwenCoder7B", device="cpu",
                                  purpose="coder", total=4, free=3, ctx=4096)
        with self.assertRaisesRegex(pm.AdmissionError, "disagree"):
            pm.validate_live_slots(snap, [backend])
        pm.validate_live_slots(snap, [SimpleNamespace(port=18182, model="QwenCoder7B",
                                                      total=4, free=4, ctx=4096)])

    def test_live_slot_evidence_rejects_duplicates_and_malformed_metadata(self):
        snap = parse(fixture())
        good = SimpleNamespace(port=18182, model="QwenCoder7B",
                               total=4, free=4, ctx=4096)
        bad = SimpleNamespace(port=18182, model="QwenCoder7B",
                              total=2, free=0, ctx=4096)
        with self.assertRaises(pm.AdmissionError):
            pm.validate_live_slots(snap, [bad, good])
        with self.assertRaises(pm.AdmissionError):
            pm.validate_live_slots(snap, [SimpleNamespace(
                port=18182, model="QwenCoder7B", total=4, free=4, ctx="4096")])
        with self.assertRaises(pm.AdmissionError):
            pm.validate_live_slots(snap, [SimpleNamespace(port=18182, model="QwenCoder7B")])
        with self.assertRaises(pm.AdmissionError):
            pm.validate_live_slots(snap, None)

    def test_invalid_json_duplicates_nonfinite_and_oversized(self):
        for raw in (b'{"schema":1,"schema":2}', b'{"schema":NaN}',
                    b'{}' * 40000, b'\xff', b'[]'):
            with self.subTest(raw=raw[:12]), self.assertRaises(pm.AdmissionError):
                pm.parse_snapshot(raw)

    def test_overlong_json_integer_is_normalized_to_admission_error(self):
        # CPython 3.11+ may raise bare ValueError before schema validation.
        # The public parser must expose only its fail-closed AdmissionError.
        raw = (b'{"schema":' + b'9' * 5000
               + b',"evidence":"synthetic-fixture","models":[]}')
        self.assertLessEqual(len(raw), pm.MAX_PROFILE_BYTES)
        with self.assertRaises(pm.AdmissionError):
            pm.parse_snapshot(raw)

    def test_host_traversal_and_unpinned_rejected(self):
        for changes in ({"endpoint": "http://localhost:18182"},
                        {"endpoint": "http://127.0.0.1:18182/path"},
                        {"endpoint": "https://127.0.0.1:18182"},
                        {"endpoint": "http://0.0.0.0:18182"},
                        {"endpoint": "http://127.0.0.1:018182"},
                        {"runtime_version": "latest"},
                        {"model": "../../private"},
                        {"model_sha256": "bad"},
                        {"slots_free": 17},
                        {"context_tokens": True},
                        {"ram_mib": -1},
                        {"device": "npu"},
                        {"transport": "remote"},
                        {"token": "secret"}):
            with self.subTest(changes=changes), self.assertRaises(pm.AdmissionError):
                parse(fixture(**changes))

    def test_strict_model_inventory_identity(self):
        obj = fixture()
        obj["models"].append(obj["models"][0].copy())
        with self.assertRaises(pm.AdmissionError):
            parse(obj)
        obj["models"][1]["id"] = "another"
        with self.assertRaises(pm.AdmissionError):
            parse(obj)

    def test_no_bool_budgets_and_no_duplicate_bursts(self):
        snap = parse(fixture())
        for kw in ({"worker_leases": True}, {"max_retries": 3},
                   {"counts": (1, 1)}, {"completion_tokens": 0},
                   {"ram_budget_mib": True}):
            with self.subTest(kw=kw), self.assertRaises(pm.AdmissionError):
                run(snap, **kw)

    def test_cli_help_does_not_import_or_invoke_any_backend(self):
        cmd = [sys.executable, str(Path(__file__).parents[1] / "tools" /
                 "spartan_provider_matrix.py"), "--help"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--snapshot", result.stdout)


if __name__ == "__main__":
    unittest.main()
