"""Opt-in local provider admission; offline stdlib-only, no real model calls."""
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
import tempfile
import unittest

from tools import spartan_budget as budget
from tools import spartan_openai_provider as provider


def response(*, model="local-model", prompt_tokens=3, output_tokens=2,
             finish_reason="stop", content="bounded candidate"):
    return {"model": model, "usage": {"prompt_tokens": prompt_tokens,
             "completion_tokens": output_tokens, "total_tokens": prompt_tokens + output_tokens},
            "choices": [{"finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": content}}]}


class LocalProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = budget.connect(Path(self.temp.name) / "budget.sqlite")
        budget.schema(self.db)
        budget.configure(self.db, 50000, 0)
        self.calls = []
        self.now = [1000.0]
        self.policy = provider.ProviderPolicy("llama_cpp", "local-model", 18182,
                                             enabled=True, max_output_tokens=16,
                                             tokens_per_second=100)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def adapter(self, policy=None, answer=None):
        def fake(p, rid, payload):
            self.calls.append((rid, payload))
            return response() if answer is None else answer
        return provider.LocalOpenAIProvider(policy or self.policy, transport=fake,
                                            clock=lambda: self.now[0])

    def call(self, adapter, rid="lease-1-attempt-1", prompt="hello"):
        return adapter.complete(db=self.db, worker=1, request_id=rid, prompt=prompt)

    def test_happy_path_exact_model_and_durable_ledger(self):
        result = self.call(self.adapter())
        self.assertEqual(result["text"], "bounded candidate")
        self.assertEqual((result["input_tokens"], result["output_tokens"]), (3, 2))
        self.assertEqual(result["reserved_tokens"], len(b"hello") + 16)
        self.assertFalse(result["verified_local_execution"])
        self.assertEqual(self.calls[0][1]["model"], "local-model")
        self.assertEqual(self.calls[0][1]["stream"], False)
        self.assertEqual(budget.status(self.db)["reported_tokens"], 5)

    def test_duplicate_id_does_not_dispatch_after_restart(self):
        self.call(self.adapter())
        with self.assertRaisesRegex(provider.ProviderDenied, "duplicate"):
            self.call(self.adapter())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(budget.status(self.db)["requests"], 1)

    def test_failed_provider_keeps_ambiguous_budget_debit(self):
        def unavailable(*args):
            raise provider.ProviderDenied("provider unavailable")
        p = provider.LocalOpenAIProvider(self.policy, transport=unavailable)
        with self.assertRaises(provider.ProviderDenied):
            self.call(p)
        self.assertEqual(budget.status(self.db)["unsettled"], 1)
        with self.assertRaisesRegex(provider.ProviderDenied, "duplicate"):
            self.call(self.adapter())
        self.assertEqual(self.calls, [])

    def test_disabled_by_default_and_malformed_policy(self):
        off = provider.LocalOpenAIProvider(provider.ProviderPolicy("llama_cpp", "model", 18182))
        with self.assertRaisesRegex(provider.ProviderDenied, "opted in"):
            self.call(off)
        for changes in ({"provider": "remote"}, {"model": "bad model"},
                        {"port": 0}, {"retries": 1}, {"max_inflight": 300},
                        {"declared_ram_mib": 5000}, {"tokens_per_second": 0}):
            values = {"provider": "llama_cpp", "model": "local-model", "port": 18182,
                      "ram_ceiling_mib": 2048}
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(provider.ProviderDenied):
                provider.ProviderPolicy(**values)
        self.assertEqual(budget.status(self.db)["requests"], 0)

    def test_prompt_bounds_are_checked_before_reservation(self):
        p = self.adapter()
        for invalid in ("", "x" * 8193, 123, None):
            with self.assertRaises(provider.ProviderDenied):
                self.call(p, prompt=invalid)
        self.assertEqual(budget.status(self.db)["requests"], 0)

    def test_rate_is_fail_closed_and_expires_after_one_minute(self):
        policy = provider.ProviderPolicy("litellm_proxy", "local-model", 4000,
                                       enabled=True, requests_per_minute=1,
                                       max_output_tokens=16)
        p = self.adapter(policy=policy)
        self.call(p, "one")
        with self.assertRaisesRegex(provider.ProviderDenied, "rate cap"):
            self.call(p, "two")
        self.now[0] += 60
        self.call(p, "two")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(budget.status(self.db)["requests"], 2)

    def test_process_concurrency_cap(self):
        entered, release = Event(), Event()
        def blocking(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return response()
        p = provider.LocalOpenAIProvider(self.policy, transport=blocking)
        errors = []
        def run():
            try:
                with closing(budget.connect(Path(self.temp.name) / "budget.sqlite")) as another:
                    p.complete(db=another, worker=1, request_id="first", prompt="hello")
            except Exception as exc:
                errors.append(exc)
        thread = Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            with self.assertRaisesRegex(provider.ProviderDenied, "concurrency"):
                self.call(p, "second")
        finally:
            release.set()
            thread.join(4)
        self.assertFalse(errors)
        self.assertEqual(budget.status(self.db)["requests"], 1)

    def test_invalid_identity_usage_and_output_do_not_pass(self):
        for i, answer in enumerate((response(model="evil"),
                                    {"model": "local-model", "choices": []},
                                    response(finish_reason="length"),
                                    response(content="x" * 70000))):
            with self.subTest(i=i), self.assertRaises(provider.ProviderDenied):
                self.call(self.adapter(answer=answer), f"invalid-{i}")
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(budget.status(self.db)["requests"], 4)

    def test_overrun_persistently_freezes_ledger(self):
        over = response(prompt_tokens=100, output_tokens=100)
        with self.assertRaisesRegex(provider.ProviderDenied, "frozen"):
            self.call(self.adapter(answer=over))
        self.assertTrue(budget.status(self.db)["frozen"])
        with self.assertRaisesRegex(provider.ProviderDenied, "budget"):
            self.call(self.adapter(), "later")

    def test_no_follow_redirect_and_no_external_host(self):
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", "http://example.invalid/steal")
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = provider.ProviderPolicy("llama_cpp", "local-model", server.server_port,
                                            enabled=True, max_output_tokens=16)
            with self.assertRaisesRegex(provider.ProviderDenied, "unavailable"):
                self.call(provider.LocalOpenAIProvider(config), "redirect")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
        self.assertEqual(budget.status(self.db)["unsettled"], 1)


if __name__ == "__main__":
    unittest.main()
