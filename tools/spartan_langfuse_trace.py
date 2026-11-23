#!/usr/bin/env python3
"""Opt-in, allowlist-only Spartan telemetry -> Langfuse OTLP/HTTP JSON.

Not a scheduler, audit authority, or instrumentation of private organisms.
No third-party dependencies, no network unless --send is explicitly supplied.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import urllib.error
import urllib.parse
import urllib.request

SCHEMA = 'mojum.spartan.telemetry/1'
PHASES = frozenset({'admission', 'provider', 'patch_test', 'pr_publish', 'qa'})
OUTCOMES = frozenset({'ok', 'error', 'reject', 'skip'})
REQUIRED = frozenset({'schema', 'event_id', 'task_id', 'attempt', 'phase',
                      'outcome', 'at_ms', 'duration_ms', 'input_tokens',
                      'output_tokens', 'cost_usd_micros'})
OPTIONAL = frozenset({'pr_head', 'ci_passed'})
EVENT_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
TASK_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')
MAX_EVENTS = 512
MAX_LINE_BYTES = 4096
MAX_PAYLOAD = 1024 * 1024
ENDPOINT = '/api/public/otel/v1/traces'


class TraceError(ValueError):
    """The event cannot pass the strict telemetry security boundary."""


def _integer(value, maximum):
    return type(value) is int and 0 <= value <= maximum


def validate(raw):
    """Return a detached, strictly typed event; reject unknown/private fields."""
    if type(raw) is not dict or not REQUIRED <= raw.keys() or raw.keys() - REQUIRED - OPTIONAL:
        raise TraceError('invalid telemetry schema fields')
    if raw['schema'] != SCHEMA or raw['phase'] not in PHASES or raw['outcome'] not in OUTCOMES:
        raise TraceError('invalid telemetry event category')
    if (type(raw['event_id']) is not str or not EVENT_ID.fullmatch(raw['event_id'])
            or type(raw['task_id']) is not str or not TASK_ID.fullmatch(raw['task_id'])):
        raise TraceError('invalid telemetry identifiers')
    if not (type(raw['attempt']) is int and 1 <= raw['attempt'] <= 1000):
        raise TraceError('invalid fencing attempt')
    for field, bound in [('at_ms', 10**15), ('duration_ms', 86_400_000),
                         ('input_tokens', 10**9), ('output_tokens', 10**9),
                         ('cost_usd_micros', 10**12)]:
        if not _integer(raw[field], bound):
            raise TraceError('invalid numeric telemetry field')
    if raw['duration_ms'] > raw['at_ms']:
        raise TraceError('duration exceeds event timestamp')
    head = raw.get('pr_head')
    if head is not None and (type(head) is not str or SHA.fullmatch(head) is None):
        raise TraceError('invalid exact PR head')
    if 'ci_passed' in raw and type(raw['ci_passed']) is not bool:
        raise TraceError('invalid CI outcome')
    if raw['phase'] in {'pr_publish', 'qa'} and head is None:
        raise TraceError('PR lifecycle requires exact head SHA')
    if raw['phase'] == 'qa' and 'ci_passed' not in raw:
        raise TraceError('QA requires CI outcome')
    if raw['phase'] != 'qa' and 'ci_passed' in raw:
        raise TraceError('CI outcome only allowed for QA')
    if raw['phase'] != 'provider' and (raw['input_tokens'] or raw['output_tokens'] or raw['cost_usd_micros']):
        raise TraceError('usage may only be reported for provider calls')
    return dict(raw)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise TraceError('duplicate JSON object key')
        value[key] = item
    return value


def _nonfinite(_):
    raise TraceError('nonfinite JSON value')


def read_events(path):
    """Open input without following a symlink; require owner-only POSIX file."""
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    descriptor = os.open(path, flags)
    try:
        mode = os.fstat(descriptor)
        if not stat.S_ISREG(mode.st_mode) or (os.name == 'posix' and
                (mode.st_uid != os.geteuid() or mode.st_mode & 0o077)):
            raise TraceError('input must be an owner-only regular file')
        events, seen = [], set()
        with os.fdopen(descriptor, 'rb') as source:
            descriptor = -1
            for line in source:
                if len(events) >= MAX_EVENTS or len(line) > MAX_LINE_BYTES:
                    raise TraceError('telemetry batch exceeds resource budget')
                try:
                    item = validate(json.loads(line.decode('utf-8'), object_pairs_hook=_unique,
                                               parse_constant=_nonfinite))
                except (UnicodeError, ValueError, TypeError, KeyError) as exc:
                    raise TraceError('invalid telemetry JSON line') from exc
                if item['event_id'] in seen:
                    raise TraceError('duplicate telemetry event id')
                seen.add(item['event_id'])
                events.append(item)
        if not events:
            raise TraceError('empty telemetry input')
        return events
    finally:
        if descriptor != -1:
            os.close(descriptor)


def summary(events):
    validated = [validate(event) for event in events]
    providers = [e for e in validated if e['phase'] == 'provider']
    qa = [e for e in validated if e['phase'] == 'qa']
    return {'schema': SCHEMA, 'events': len(validated),
            'provider_calls_reported': len(providers),
            'input_tokens_reported': sum(e['input_tokens'] for e in providers),
            'output_tokens_reported': sum(e['output_tokens'] for e in providers),
            'cost_usd_micros_reported': sum(e['cost_usd_micros'] for e in providers),
            'qa_rejects_reported': sum(e['outcome'] == 'reject' for e in qa),
            'ci_green_qa_rejects_reported': sum(e['ci_passed'] and e['outcome'] == 'reject' for e in qa)}


def _attr(name, value):
    if type(value) is bool:
        item = {'boolValue': value}
    elif type(value) is int:
        item = {'intValue': str(value)}
    else:
        item = {'stringValue': value}
    return {'key': name, 'value': item}


def build_payload(events, hmac_key):
    """Build deterministic OTLP JSON without input/output/prompt/host contents."""
    if type(hmac_key) is not bytes or len(hmac_key) < 32:
        raise TraceError('telemetry HMAC key must contain at least 32 bytes')
    if not 1 <= len(events) <= MAX_EVENTS:
        raise TraceError('invalid OTLP batch size')
    seen = set()
    spans = []
    for source in events:
        event = validate(source)
        if event['event_id'] in seen:
            raise TraceError('duplicate telemetry event id')
        seen.add(event['event_id'])
        # Fencing identity and event identity cannot appear in exported plaintext.
        trace_material = f"{event['task_id']}\0{event['attempt']}".encode('utf-8')
        trace = hmac.new(hmac_key, b'trace\0' + trace_material, hashlib.sha256).hexdigest()[:32]
        span = hmac.new(hmac_key, b'span\0' + event['event_id'].encode('ascii'), hashlib.sha256).hexdigest()[:16]
        attrs = [_attr('mojum.telemetry.schema', SCHEMA),
                 _attr('mojum.phase', event['phase']),
                 _attr('mojum.outcome', event['outcome']),
                 _attr('mojum.fence.attempt', event['attempt']),
                 _attr('mojum.duration_ms', event['duration_ms']),
                 _attr('mojum.tokens.input', event['input_tokens']),
                 _attr('mojum.tokens.output', event['output_tokens']),
                 _attr('mojum.cost.usd_micros', event['cost_usd_micros'])]
        if event.get('pr_head') is not None:
            attrs.append(_attr('mojum.pr.head_sha', event['pr_head']))
        if 'ci_passed' in event:
            attrs.append(_attr('mojum.ci.passed_reported', event['ci_passed']))
        start = (event['at_ms'] - event['duration_ms']) * 1_000_000
        end = event['at_ms'] * 1_000_000
        spans.append({'traceId': trace, 'spanId': span, 'name': 'mojum.' + event['phase'],
                      'kind': 1, 'startTimeUnixNano': str(start), 'endTimeUnixNano': str(end),
                      'attributes': attrs,
                      'status': {'code': 2 if event['outcome'] in {'reject', 'error'} else 1}})
    payload = {'resourceSpans': [{'resource': {'attributes': [_attr('service.name', 'mojum-spartan')]},
                                  'scopeSpans': [{'scope': {'name': 'mojum.spartan.safe-telemetry',
                                                            'version': '1'}, 'spans': spans}]}]}
    encoded = json.dumps(payload, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    if len(encoded) > MAX_PAYLOAD:
        raise TraceError('OTLP payload exceeds resource budget')
    return encoded


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise TraceError('redirects forbidden for trace export')


def _endpoint(base_url):
    parsed = urllib.parse.urlsplit(base_url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in ('', '/')):
        raise TraceError('trace endpoint must be an HTTPS origin without paths or credentials')
    if parsed.hostname in ('localhost', '127.0.0.1', '::1'):
        raise TraceError('use a trusted HTTPS host, not loopback')
    return base_url.rstrip('/') + ENDPOINT


def send(payload, *, base_url, public_key, secret_key, opener=None):
    """Network is reached ONLY by explicit caller action. No redirects/retries."""
    if type(payload) is not bytes or not payload or len(payload) > MAX_PAYLOAD:
        raise TraceError('invalid OTLP body')
    endpoint = _endpoint(base_url)
    if not public_key or not secret_key or '\n' in public_key + secret_key:
        raise TraceError('missing or invalid Langfuse credentials')
    auth = base64.b64encode((public_key + ':' + secret_key).encode('utf-8')).decode('ascii')
    request = urllib.request.Request(endpoint, data=payload, method='POST', headers={
        'Authorization': 'Basic ' + auth,
        'Content-Type': 'application/json',
        'x-langfuse-ingestion-version': '4'})
    transport = opener if opener is not None else urllib.request.build_opener(_NoRedirect())
    try:
        with transport.open(request, timeout=5) as response:
            if not 200 <= response.status < 300:
                raise TraceError('OTLP server rejected telemetry')
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TraceError('OTLP transport failed (details intentionally redacted)') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='0600 owner-only telemetry JSONL')
    parser.add_argument('--send', action='store_true', help='EXPLICITLY send allowlisted OTLP spans')
    parser.add_argument('--base-url', help='HTTPS origin of trusted Langfuse instance')
    args = parser.parse_args(argv)
    try:
        events = read_events(args.input)
        result = summary(events)
        if args.send:
            key = os.environ.get('MOJUM_TRACE_HMAC_KEY', '').encode('utf-8')
            payload = build_payload(events, key)
            send(payload, base_url=args.base_url or '',
                 public_key=os.environ.get('LANGFUSE_PUBLIC_KEY', ''),
                 secret_key=os.environ.get('LANGFUSE_SECRET_KEY', ''))
            result['exported'] = True
        else:
            result['exported'] = False
        print(json.dumps(result, sort_keys=True))
        return 0
    except (TraceError, OSError) as exc:
        # Avoid exception string: any transport or filename may contain secrets.
        print('telemetry rejected: ' + type(exc).__name__, file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
