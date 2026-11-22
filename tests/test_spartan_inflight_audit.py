"""Fail-closed, CPU-only tests for provider-boundary concurrency auditing."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.spartan_inflight_audit import AuditError, audit, main


def start(at, rid, worker='w1', model='gemini-3.5-flash-lite'):
    return {'at_ms': at, 'kind': 'start', 'request_id': rid,
            'worker': worker, 'model': model}


def end(at, rid, worker='w1', outcome='ok', model='gemini-3.5-flash-lite'):
    entry = {**start(at, rid, worker, model), 'kind': 'finish',
             'outcome': outcome, 'input_tokens': 13,
             'output_tokens': 7, 'cost_usd': '0.001234'}
    if outcome == 'ok':
        entry['response_id'] = 'response-' + rid
    return entry


class SpartanInflightAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'events.jsonl'

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, events):
        self.path.write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')
        return self.path

    def test_300_completed_overlapping_successful_calls(self):
        events = [start(100, f'r{i}', f'w{i}') for i in range(1, 301)]
        events += [end(200, f'r{i}', f'w{i}') for i in range(1, 301)]
        result = audit(self.write(events))
        self.assertTrue(result['target_met_in_log'])
        self.assertEqual(result['successful_peak']['peak_distinct_workers'], 300)
        self.assertEqual(result['completed'], 300)
        self.assertEqual(result['cost_usd'], '0.370200')
        self.assertEqual((result['input_tokens'], result['output_tokens']), (3900, 2100))

    def test_300_leases_or_starts_without_finishes_are_not_proof(self):
        self.write([start(100, f'r{i}', f'w{i}') for i in range(1, 301)])
        result = audit(self.path)
        self.assertEqual(result['unfinished'], 300)
        self.assertFalse(result['target_met_in_log'])
        self.assertEqual(result['successful_peak']['peak_distinct_workers'], 0)

    def test_same_worker_many_concurrent_calls_is_not_300_workers(self):
        self.write([start(100, f'r{i}') for i in range(300)] +
                   [end(200, f'r{i}') for i in range(300)])
        result = audit(self.path)
        self.assertEqual(result['successful_peak']['peak_requests'], 300)
        self.assertEqual(result['successful_peak']['peak_distinct_workers'], 1)
        self.assertFalse(result['target_met_in_log'])

    def test_nonoverlapping_slices_do_not_claim_parallelism(self):
        self.write([start(100, 'a'), end(200, 'a'),
                    start(200, 'b', 'w2'), end(300, 'b', 'w2')])
        self.assertEqual(audit(self.path)['successful_peak']['peak_distinct_workers'], 1)

    def test_errors_do_not_qualify_as_successful_provider_calls(self):
        self.write([start(100, 'a'), start(100, 'b', 'w2'),
                    end(200, 'a'), end(200, 'b', 'w2', 'error')])
        result = audit(self.path, target=2)
        self.assertEqual(result['completed_peak_including_errors']['peak_distinct_workers'], 2)
        self.assertEqual(result['successful_peak']['peak_distinct_workers'], 1)
        self.assertFalse(result['target_met_in_log'])

    def test_decimal_cost_exact(self):
        self.write([start(1, 'a'), end(2, 'a'), start(3, 'b'), end(4, 'b')])
        self.assertEqual(audit(self.path)['cost_usd'], '0.002468')

    def test_reject_replay_duplicate_response_or_request(self):
        fixtures = [
            [start(1, 'a'), start(2, 'a')],
            [start(1, 'a'), end(2, 'a'), start(3, 'a')],
            [start(1, 'a'), end(2, 'a'), start(3, 'b'),
             {**end(4, 'b'), 'response_id': 'response-a'}],
        ]
        for events in fixtures:
            with self.subTest(events=events):
                with self.assertRaises(AuditError):
                    audit(self.write(events))

    def test_reject_mismatched_identity_and_impossible_clock(self):
        fixtures = [
            [end(1, 'a')],
            [start(1, 'a'), end(2, 'a', 'w2')],
            [start(1, 'a'), end(2, 'a', model='other')],
            [start(2, 'a'), end(2, 'a')],
            [start(2, 'a'), end(1, 'a')],
            [start(10, 'a'), start(9, 'b')],
        ]
        for events in fixtures:
            with self.subTest(events=events):
                with self.assertRaises(AuditError):
                    audit(self.write(events))

    def test_reject_untrusted_extra_fields_and_invalid_accounting(self):
        bad = [
            {**start(1, 'a'), 'prompt': 'SECRET'},
            {**start(1, 'a'), 'at_ms': True},
            {**start(1, 'a'), 'worker': 'a/../../etc'},
            {**end(2, 'a'), 'cost_usd': 0.1},
            {**end(2, 'a'), 'cost_usd': '-0.1'},
            {**end(2, 'a'), 'input_tokens': -1},
            {**end(2, 'a'), 'response_id': ''},
            {**end(2, 'a'), 'model': 'A\nB'},
        ]
        for invalid in bad:
            events = [start(1, 'a'), invalid] if invalid['kind'] == 'finish' else [invalid]
            with self.subTest(invalid=invalid):
                with self.assertRaises(AuditError):
                    audit(self.write(events))

    def test_reject_json_duplicate_key_invalid_utf8_and_limits(self):
        for raw in ('{"kind":"start","kind":"finish"}\n',
                    '{"kind":NaN}\n', '{}\n', '\n'):
            with self.subTest(raw=raw):
                self.path.write_text(raw)
                with self.assertRaises(AuditError):
                    audit(self.path)
        self.path.write_bytes(b'\xff\n')
        with self.assertRaises(AuditError):
            audit(self.path)
        self.write([start(1, 'a'), end(2, 'a'), start(3, 'b')])
        with self.assertRaises(AuditError):
            audit(self.path, max_events=2)
        self.path.write_bytes(b' ' * 4097 + b'\n')
        with self.assertRaises(AuditError):
            audit(self.path)

    def test_cli_target_exit_status_and_invalid_logs(self):
        self.write([start(1, 'a'), end(2, 'a')])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--input', str(self.path), '--target', '2', '--require-target']), 1)
            self.assertEqual(main(['--input', str(self.path), '--target', '1', '--require-target']), 0)
            self.assertEqual(main(['--input', str(self.path), '--target', '301']), 2)


if __name__ == '__main__':
    unittest.main()
