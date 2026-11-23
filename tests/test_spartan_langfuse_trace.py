"""Offline regression boundaries for the opt-in Langfuse OTLP adapter."""
from __future__ import annotations
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.spartan_langfuse_trace import (TraceError, validate, read_events,
                                          summary, build_payload, send, main)

KEY = b'k' * 32


def event(**updates):
    data = dict(schema='mojum.spartan.telemetry/1', event_id='evt-1', task_id='issue-697',
                attempt=2, phase='provider', outcome='ok', at_ms=1760000000000,
                duration_ms=25, input_tokens=13, output_tokens=7,
                cost_usd_micros=23)
    data.update(updates)
    return data


class TraceTests(unittest.TestCase):
    def test_strict_redaction_and_otlp_determinism(self):
        raw = event(task_id='sensitive-worker-task', event_id='sensitive-id')
        a = build_payload([raw], KEY)
        self.assertEqual(a, build_payload([raw], KEY))
        obj = json.loads(a)
        span = obj['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
        self.assertEqual(len(span['traceId']), 32)
        self.assertEqual(len(span['spanId']), 16)
        self.assertEqual(span['name'], 'mojum.provider')
        self.assertNotIn(b'sensitive-worker-task', a)
        self.assertNotIn(b'sensitive-id', a)
        self.assertNotIn(b'issue-697', a)

    def test_forbid_prompts_tokens_private_and_unknown_fields(self):
        for forbidden in ('prompt', 'source_code', 'api_key', 'private_memory',
                          'host', 'input', 'output', 'worker'):
            with self.subTest(field=forbidden), self.assertRaises(TraceError):
                validate(event(**{forbidden: 'PRIVATE data'}))

    def test_invalid_types_and_numeric_bounds(self):
        for patch_data in ({'input_tokens': True}, {'cost_usd_micros': -1},
                           {'duration_ms': 86400001}, {'attempt': 0},
                           {'at_ms': float('nan')}, {'event_id': 'bad id'},
                           {'pr_head': 'xyz'}, {'phase': 'unknown'},
                           {'ci_passed': False}):
            with self.subTest(patch_data=patch_data), self.assertRaises(TraceError):
                validate(event(**patch_data))

    def test_pr_qa_and_reported_false_success(self):
        qa = event(phase='qa', outcome='reject', pr_head='a'*40, ci_passed=True,
                   input_tokens=0, output_tokens=0, cost_usd_micros=0)
        self.assertEqual(summary([qa])['ci_green_qa_rejects_reported'], 1)
        data = build_payload([qa], KEY)
        self.assertIn(b'mojum.pr.head_sha', data)
        self.assertIn(b'ci.passed_reported', data)
        with self.assertRaises(TraceError):
            validate(event(phase='qa'))

    def test_fencing_and_event_ids_are_distinct(self):
        a = build_payload([event()], KEY)
        b = build_payload([event(attempt=3)], KEY)
        get_span = lambda payload: json.loads(payload)['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
        self.assertNotEqual(get_span(a)['traceId'], get_span(b)['traceId'])
        self.assertNotEqual(get_span(a)['traceId'], get_span(build_payload([event(task_id='other')], KEY))['traceId'])
        with self.assertRaises(TraceError):
            build_payload([event(), event()], KEY)
        with self.assertRaises(TraceError):
            build_payload([event()], b'weak')

    def test_input_deny_duplicate_json_and_private_modes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'telemetry.jsonl'
            path.write_text(json.dumps(event()) + '\n', encoding='utf-8')
            path.chmod(0o600)
            self.assertEqual(len(read_events(path)), 1)
            path.write_text('{"schema":"x","schema":"y"}\n', encoding='utf-8')
            with self.assertRaises(TraceError):
                read_events(path)
            path.write_text(json.dumps(event()) + '\n', encoding='utf-8')
            path.chmod(0o644)
            if os.name == 'posix':
                with self.assertRaises(TraceError):
                    read_events(path)
            path.chmod(0o600)
            alias = Path(root) / 'alias'
            alias.symlink_to(path)
            with self.assertRaises((TraceError, OSError)):
                read_events(alias)

    def test_default_cli_never_exports(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'input.jsonl'
            path.write_text(json.dumps(event()) + '\n', encoding='utf-8')
            path.chmod(0o600)
            with patch('tools.spartan_langfuse_trace.send') as sender:
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(main(['--input', str(path)]), 0)
                sender.assert_not_called()
                self.assertFalse(json.loads(out.getvalue())['exported'])

    def test_send_uses_langfuse_v4_otlp_json_only_on_explicit_call(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return None
        class FakeOpener:
            def __init__(self): self.requests = []
            def open(self, req, timeout):
                self.requests.append((req, timeout))
                return Response()
        fake = FakeOpener()
        payload = build_payload([event()], KEY)
        send(payload, base_url='https://lf.example.org', public_key='public', secret_key='secret', opener=fake)
        request, timeout = fake.requests[0]
        self.assertEqual(request.full_url, 'https://lf.example.org/api/public/otel/v1/traces')
        self.assertEqual(request.get_header('X-langfuse-ingestion-version'), '4')
        self.assertEqual(timeout, 5)
        self.assertNotIn(b'secret', request.data)
        for target in ('http://lf.example.org', 'https://u:p@lf.example.org',
                       'https://lf.example.org/evil', 'https://lf.example.org?x=1'):
            with self.subTest(target=target), self.assertRaises(TraceError):
                send(payload, base_url=target, public_key='p', secret_key='s', opener=fake)

    def test_event_and_payload_limits(self):
        with self.assertRaises(TraceError):
            build_payload([event(event_id='e'+str(i)) for i in range(513)], KEY)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'too-large.jsonl'
            path.write_text('x'*5000 + '\n', encoding='utf-8')
            path.chmod(0o600)
            with self.assertRaises(TraceError):
                read_events(path)


if __name__ == '__main__':
    unittest.main()
