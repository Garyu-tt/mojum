"""Deterministic admission proof and actual localhost HTTP smoke; no model needed."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from threading import Event, Thread
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import spartan_provider_admission as ad


def provider(**changes):
    data = dict(kind="llama_cpp", port=18182, model_id="test-coder.gguf",
                max_concurrent_calls=2, max_completion_tokens=512,
                min_context_tokens=1024, ram_per_call_mib=64,
                vram_per_call_mib=0, price_input_micro_usd_per_1k=100,
                price_output_micro_usd_per_1k=200)
    data.update(changes)
    return ad.Provider(**data)


def fake_request(port, path, body=None, timeout=5):
    if path == "/v1/models":
        return {"data": [{"id": "test-coder.gguf"}]}
    if path == "/health":
        return {"status": "ok"}
    if path == "/slots":
        return [{"id": 0, "is_processing": False, "n_ctx": 4096},
                {"id": 1, "is_processing": False, "n_ctx": 4096}]
    if path == "/v1/chat/completions":
        return {"model": body["model"], "choices": [
            {"finish_reason": "stop", "message": {"content": "proposal only"}}],
            "usage": {"completion_tokens": 7}}
    raise AssertionError("unsupported test API")


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.p = provider()
        self.headroom = ad.Headroom(ram_mib=128, vram_mib=0,
                                    max_cost_micro_usd=500)
        self.proof = ad.probe(self.p, request=fake_request)

    def permit(self, **changes):
        inputs = dict(calls=2, prompt_tokens_cap=100,
                      completion_tokens_cap=100, headroom=self.headroom)
        inputs.update(changes)
        return ad.admit(self.p, self.proof, **inputs)

    def test_two_observed_slots_budget_and_versioned_contract(self):
        permit = self.permit()
        self.assertEqual((permit.schema, permit.admitted_calls),
                         (ad.SCHEMA, 2))
        self.assertEqual(permit.reserved_token_budget, 400)
        self.assertEqual(permit.estimated_cost_micro_usd, 60)
        self.assertEqual(permit.proof, "live-llama-slots")

    def test_live_busy_slots_reject_parallel_overcommit(self):
        def busy(port, path, body=None, timeout=5):
            value = fake_request(port, path, body, timeout)
            if path == "/slots":
                value[1]["is_processing"] = True
            return value
        proof = ad.probe(self.p, request=busy)
        with self.assertRaisesRegex(ad.AdmissionError, "physical free slots"):
            ad.admit(
                self.p, proof, calls=2, prompt_tokens_cap=100,
                completion_tokens_cap=100, headroom=self.headroom)

    def test_no_headroom_no_usd_budget_no_context(self):
        with self.assertRaisesRegex(ad.AdmissionError, "RAM headroom"):
            self.permit(headroom=ad.Headroom(127, 0, 500))
        with self.assertRaisesRegex(ad.AdmissionError, "cost allowance"):
            self.permit(headroom=ad.Headroom(128, 0, 59))
        with self.assertRaisesRegex(ad.AdmissionError, "context"):
            self.permit(prompt_tokens_cap=4000, completion_tokens_cap=100)
        with self.assertRaisesRegex(ad.AdmissionError, "output tokens"):
            self.permit(completion_tokens_cap=513)
        with self.assertRaisesRegex(ad.AdmissionError, "VRAM headroom"):
            ad.admit(provider(vram_per_call_mib=1), self.proof,
                     calls=1, prompt_tokens_cap=100,
                     completion_tokens_cap=100, headroom=self.headroom)

    def test_strict_numeric_and_model_identity(self):
        for patch_value in ({"port": True}, {"port": 5000},
                            {"model_id": "http://localhost/x"},
                            {"max_concurrent_calls": 300},
                            {"ram_per_call_mib": False},
                            {"price_input_micro_usd_per_1k": -1}):
            with self.subTest(patch=patch_value), self.assertRaises(ad.AdmissionError):
                provider(**patch_value)
        with self.assertRaises(ad.AdmissionError):
            ad.admit(self.p, self.proof, calls=True, prompt_tokens_cap=100,
                     completion_tokens_cap=100, headroom=self.headroom)

    def test_backend_model_discovery_fail_closed(self):
        for models in ({"data": []}, {"data": [{"id": "wrong"}]},
                       {"data": [{"id": "x"}, {"id": "x"}]},
                       {"data": [{"id": 123}]}):
            def altered(port, path, body=None, timeout=5):
                return models if path == "/v1/models" else fake_request(port, path, body, timeout)
            with self.subTest(models=models), self.assertRaises(ad.AdmissionError):
                ad.probe(self.p, request=altered)

    def test_bad_slot_metadata_and_health_rejected(self):
        def check(slots=None, health=None):
            def request(port, path, body=None, timeout=5):
                if path == "/slots":
                    return slots
                if path == "/health":
                    return health if health is not None else {"status": "ok"}
                return fake_request(port, path, body, timeout)
            return request
        bad = [[{"id": 0, "is_processing": 0, "n_ctx": 4096}],
               [{"id": 0, "is_processing": False, "n_ctx": 4096},
                {"id": 0, "is_processing": False, "n_ctx": 4096}],
               [{"id": 0, "is_processing": False, "n_ctx": 128}],
               [{"id": "0", "is_processing": False, "n_ctx": 4096}]]
        for slots in bad:
            with self.subTest(slots=slots), self.assertRaises(ad.AdmissionError):
                ad.probe(self.p, request=check(slots))
        with self.assertRaisesRegex(ad.AdmissionError, "not ready"):
            ad.probe(self.p, request=check([], {"status": "loading"}))

    def test_litellm_catalog_is_not_proof_of_local_routing(self):
        p = provider(kind="litellm", port=4000)
        proof = ad.probe(p, request=fake_request)
        self.assertIsNone(proof.available_slots)
        self.assertEqual(proof.slot_proof, "unverified-proxy")
        with self.assertRaisesRegex(ad.AdmissionError, "no verified local"):
            ad.admit(p, proof, calls=1, prompt_tokens_cap=100,
                     completion_tokens_cap=100, headroom=self.headroom)
        with self.assertRaises(ad.AdmissionError):
            ad.Session(p, request=fake_request).complete(
                "hello", prompt_tokens_cap=100, completion_tokens_cap=100,
                headroom=self.headroom)

    def test_real_call_result_validation(self):
        session = ad.Session(self.p, request=fake_request)
        actual = session.complete("propose a simple patch", prompt_tokens_cap=100,
                                  completion_tokens_cap=100,
                                  headroom=self.headroom)
        self.assertEqual(actual, "proposal only")
        for mutation in ({"model": "other"}, {"choices": []},
                         {"choices": [{"message": {"content": " "}}]},
                         {"usage": {"completion_tokens": 101}},
                         {"usage": {"prompt_tokens": 101}},
                         {"choices": [{"finish_reason": "length",
                                      "message": {"content": "truncated"}}]},
                         {"choices": [{"finish_reason": "tool_calls",
                                      "message": {"content": "text",
                                                  "tool_calls": [{"type": "function"}]}}]},
                         {"choices": [{"finish_reason": "stop",
                                      "message": {"content": "text", "tool_calls": []}}]},
                         {"choices": [{"finish_reason": "stop",
                                      "message": {"content": "text", "function_call": {}}}]}):
            def changed(port, path, body=None, timeout=5):
                out = fake_request(port, path, body, timeout)
                if path == "/v1/chat/completions":
                    out.update(mutation)
                return out
            with self.subTest(mutation=mutation), self.assertRaises(ad.AdmissionError):
                ad.Session(self.p, request=changed).complete(
                    "propose a patch", prompt_tokens_cap=100,
                    completion_tokens_cap=100, headroom=self.headroom)

    def test_limited_parallel_semaphore_and_release_after_failure(self):
        start, finish = Event(), Event()
        p = provider(max_concurrent_calls=1)
        def request(port, path, body=None, timeout=5):
            if path == "/v1/chat/completions":
                start.set()
                if not finish.wait(4):
                    raise RuntimeError("test timeout")
            return fake_request(port, path, body, timeout)
        session = ad.Session(p, request=request)
        results = []
        t = Thread(target=lambda: results.append(session.complete(
            "one", prompt_tokens_cap=100, completion_tokens_cap=100,
            headroom=self.headroom)))
        t.start()
        self.assertTrue(start.wait(2))
        with self.assertRaisesRegex(ad.AdmissionError, "parallel-call cap"):
            session.complete("two", prompt_tokens_cap=100,
                             completion_tokens_cap=100, headroom=self.headroom)
        finish.set()
        t.join(3)
        self.assertFalse(t.is_alive())
        self.assertEqual(results, ["proposal only"])

    def test_concurrent_sessions_share_one_endpoint_semaphore(self):
        start, finish = Event(), Event()
        p = provider(max_concurrent_calls=1)
        def request(port, path, body=None, timeout=5):
            if path == "/v1/chat/completions":
                start.set()
                if not finish.wait(4):
                    raise RuntimeError("test timeout")
            return fake_request(port, path, body, timeout)
        first = ad.Session(p, request=request)
        second = ad.Session(p, request=request)
        self.assertIs(first._semaphore, second._semaphore)
        # A second configuration must not reset or enlarge the same port gate.
        with self.assertRaisesRegex(ad.AdmissionError, "conflicting"):
            ad.Session(provider(max_concurrent_calls=2))
        values = []
        worker = Thread(target=lambda: values.append(first.complete(
            "first", prompt_tokens_cap=100, completion_tokens_cap=100,
            headroom=self.headroom)))
        worker.start()
        try:
            self.assertTrue(start.wait(2))
            with self.assertRaisesRegex(ad.AdmissionError, "parallel-call cap"):
                second.complete("second", prompt_tokens_cap=100,
                                completion_tokens_cap=100, headroom=self.headroom)
        finally:
            finish.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(values, ["proposal only"])
        self.assertEqual(second.complete("third", prompt_tokens_cap=100,
                         completion_tokens_cap=100, headroom=self.headroom), "proposal only")

    def test_caller_cannot_understate_prompt_token_budget(self):
        with self.assertRaisesRegex(ad.AdmissionError, "token ceiling cannot cover"):
            ad.Session(self.p, request=fake_request).complete(
                "x" * 100, prompt_tokens_cap=100, completion_tokens_cap=10,
                headroom=self.headroom)

    def test_forged_slot_counts_cannot_authorize_unbounded_calls(self):
        fake = ad.Observation(self.p.model_id, 99999, 4096, "live-llama-slots")
        with self.assertRaises(ad.AdmissionError):
            ad.admit(self.p, fake, calls=1, prompt_tokens_cap=100,
                     completion_tokens_cap=100, headroom=self.headroom)

    def test_duplicate_nonfinite_and_oversize_json_fail_closed(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{' + b'"a":' + b'[' * 2000,
                    b'"' * (ad.MAX_JSON_BYTES + 1)):
            with self.subTest(length=len(raw)), self.assertRaises(ad.AdmissionError):
                ad._decode_json(raw)

    def test_invalid_transport_path_fails_before_network(self):
        for port, path, body in ((443, "/v1/models", None),
                                 (18182, "http://example.com", None),
                                 (18182, "/v1/models", {"data": 1}),
                                 (18182, "/v1/chat/completions", None)):
            with self.subTest(port=port, path=path), self.assertRaises(ad.AdmissionError):
                ad.local_http(port, path, body)


class LocalHTTPTests(unittest.TestCase):
    """Exercise actual urllib loopback wire, fixed methods and no redirects."""
    def test_actual_loopback_probe_and_completion(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                responses = {
                    "/v1/models": {"data": [{"id": "test-coder.gguf"}]},
                    "/health": {"status": "ok"},
                    "/slots": [{"id": 0, "is_processing": False, "n_ctx": 4096}],
                }
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://example.org")
                    self.end_headers()
                    return
                raw = json.dumps(responses[self.path]).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert self.path == "/v1/chat/completions"
                raw = json.dumps({"model": data["model"], "choices": [
                    {"finish_reason": "stop",
                     "message": {"content": "real localhost mock"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(raw)
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            with patch.object(ad, "LLAMA_PORTS", frozenset((port,))):
                p = provider(port=port)
                s = ad.Session(p)
                self.assertEqual(s.complete("local mock", prompt_tokens_cap=100,
                    completion_tokens_cap=100,
                    headroom=ad.Headroom(64, 0, 100)), "real localhost mock")
            server.shutdown()
            thread.join(3)


if __name__ == "__main__":
    unittest.main()
