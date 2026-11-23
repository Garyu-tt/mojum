"""No network required; live Langfuse disabled by default."""
import base64
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import unittest

from mojum.langfuse_otel import (SCHEMA, TelemetryError, send, summarize, to_otlp, validate)


LEASE = "a0b9a623-46eb-4cbf-8a76-e65ab2e55ab0"
EID = "ad68a14b-ea2a-49f5-9f87-e4f6063f44db"
HEAD = "c" * 40


def event(phase="ci", outcome="passed", **updates):
    e = dict(schema=SCHEMA, event_id=EID, lease_id=LEASE, attempt=2,
             phase=phase, outcome=outcome, timestamp_ms=1_800_000_000_000)
    if phase in ("patch", "ci", "qa"):
        e.update(pr_number=42, pr_head=HEAD)
    e.update(updates)
    return e


class LangfuseOTLPTests(unittest.TestCase):
    def test_deterministic_trace_span_and_exact_head(self):
        raw = event()
        p = to_otlp(raw)
        self.assertEqual(to_otlp(raw), p)
        span = p["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        self.assertEqual(len(span["traceId"]), 32)
        self.assertEqual(len(span["spanId"]), 16)
        self.assertEqual(span["name"], "mojum.ci")
        attrs = {a["key"]: next(iter(a["value"].values())) for a in span["attributes"]}
        self.assertEqual(attrs["mojum.pr_head"], HEAD)
        self.assertNotIn(LEASE, json.dumps(p))
        self.assertNotIn(EID, json.dumps(p))
        self.assertNotIn("langfuse.observation.input", attrs)
        self.assertNotIn("langfuse.observation.output", attrs)

    def test_noop_avoids_validation_and_transport(self):
        self.assertFalse(send({"raw_private_secret": "NEVER SEND"}))

    def test_wrong_fields_and_private_payload_denied(self):
        for updates in ({"prompt": "PRIVATE"}, {"memory": {}},
                        {"exception_text": "sk-secret"}, {"metadata": {}},
                        {"model": "model-with-key"}):
            with self.subTest(updates=updates), self.assertRaises(TelemetryError):
                validate(event(**updates))

    def test_strict_fencing_and_sha(self):
        bad = [{"attempt": True}, {"attempt": 0}, {"event_id": LEASE.upper()},
               {"lease_id": "not-a-uuid"}, {"pr_head": "A" * 40},
               {"pr_head": "d" * 39}, {"pr_number": False},
               {"timestamp_ms": float("nan")}, {"duration_ms": 3.1},
               {"outcome": "accepted"}, {"schema": "legacy"}]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(TelemetryError):
                validate(event(**item))
        with self.assertRaises(TelemetryError):
            validate(event("qa", "blocked", pr_head=None))

    def test_provider_metrics_and_isolation(self):
        p = event("provider_result", "ok", input_tokens=77, output_tokens=2,
                  cost_microusd=9, duration_ms=19, model_class="cpu_local")
        safe = validate(p)
        self.assertEqual(safe["input_tokens"], 77)
        for change in (dict(input_tokens=-1), dict(input_tokens=True),
                       dict(cost_microusd=10**13), dict(model_class="secret")):
            with self.subTest(change=change), self.assertRaises(TelemetryError):
                validate(event("provider_result", "ok", **change))
        with self.assertRaises(TelemetryError):
            validate(event("ci", "passed", input_tokens=99))
        self.assertNotIn("input_tokens", validate(event("provider_result", "failed")))

    def test_type_spoof_cannot_escape_privacy_allowlist(self):
        # Library callers may provide Python objects, not only decoded JSON.
        # Loose equality alone can admit a str subclass whose __str__ then
        # exposes arbitrary text through the OTLP string attribute.
        class Spoof(str):
            def __eq__(self, other):
                return True

            def __str__(self):
                return "PRIVATE_PROMPT_MUST_NOT_LEAVE_PROCESS"

        injected = event("provider_result", "ok", model_class=Spoof("unsafe"))
        with self.assertRaises(TelemetryError):
            validate(injected)
        with self.assertRaises(TelemetryError):
            to_otlp(injected)
        with self.assertRaises(TelemetryError):
            validate(event(schema=Spoof("unsafe")))
        with self.assertRaises(TelemetryError):
            validate(event("provider_result", "ok", model_class=7))

    def test_summary_exact_head_and_false_success(self):
        q = event("qa", "rejected", event_id="b2367903-7a72-46e1-908c-6150df5042c5")
        other = event("qa", "rejected", event_id="5211938d-04a7-4d0d-bf55-9e7996417743", pr_head="d" * 40)
        report = summarize([event(), q, other, event("provider_result", "ok", input_tokens=17)])
        self.assertEqual(report["ci_pass_qa_fail_exact_head"], 1)
        self.assertEqual(report["reported_totals"]["input_tokens"], 17)
        self.assertEqual(report["reported_counts"]["output_tokens"], 0)

    def test_network_guard(self):
        for host in ("http://example.com", "http://localhost:3000", "ftp://example.com",
                     "https://user:password@example.com", "https://example.com/evil",
                     "https://example.com?x=1", "http://10.0.0.1:3000"):
            with self.subTest(host=host), self.assertRaises(TelemetryError):
                send(event(), enabled=True, host=host, public_key="pk-demo", secret_key="sk-demo")

    def test_real_local_otlp_http_and_headers(self):
        recorded = {}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                recorded["path"] = self.path
                recorded["headers"] = self.headers
                recorded["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        try:
            self.assertTrue(send(event(), enabled=True, host=f"http://127.0.0.1:{server.server_port}",
                                 public_key="pk-demo", secret_key="sk-demo"))
            thread.join(timeout=2)
        finally:
            server.server_close()
        self.assertEqual(recorded["path"], "/api/public/otel/v1/traces")
        self.assertEqual(recorded["headers"]["x-langfuse-ingestion-version"], "4")
        self.assertEqual(base64.b64decode(recorded["headers"]["Authorization"][6:]), b"pk-demo:sk-demo")
        self.assertEqual(recorded["body"], to_otlp(event()))

    def test_local_rejects_partial_ack(self):
        from unittest.mock import patch
        class BadResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return None
            def read(self, count): return b'{"partialSuccess":{"rejectedSpans":1}}'
        class BadOpener:
            def open(self, *a, **kw): return BadResponse()
        with patch("mojum.langfuse_otel.build_opener", return_value=BadOpener()):
            with self.assertRaises(TelemetryError):
                send(event(), enabled=True, host="https://example.com",
                     public_key="pk-demo", secret_key="sk-demo")


if __name__ == "__main__":
    unittest.main()
