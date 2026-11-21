#!/usr/bin/env python3
"""Bounded, credential-free loopback inference canary for GPU Legion.

Never starts model servers, downloads model files, touches ALT, runs patches,
or performs any GitHub action. Only calls explicitly supplied 127.0.0.1 URLs.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_ENDPOINTS = 8
MAX_SAMPLES = 64
MAX_PARALLEL = 16
MAX_REPLY_BYTES = 65536
CANARY_PROMPT = "Reply with exactly two words: hello world."


class CanaryError(ValueError):
    """A configuration or response fails the bounded canary contract."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CanaryError("HTTP redirect refused")


def local_endpoint(url: str) -> str:
    if not isinstance(url, str) or len(url) > 180:
        raise CanaryError("endpoint must be a short URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise CanaryError("invalid endpoint port") from exc
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or port is None or not 1024 <= port <= 65535
            or parsed.netloc != "127.0.0.1:" + str(port)
            or parsed.path != "/v1/chat/completions"
            or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise CanaryError("only explicit numeric loopback HTTP model endpoints allowed")
    return url


def _tokens(value, name):
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 1000000:
        raise CanaryError("invalid " + name + " token count")
    return value


def call_once(endpoint: str, model: str, timeout: float, *, opener=None) -> dict:
    """Issue one constant harmless prompt; do not emit the generated text."""
    local_endpoint(endpoint)
    if not isinstance(model, str) or not model or len(model) > 128:
        raise CanaryError("model identifier invalid")
    if type(timeout) not in (int, float) or not 1 <= timeout <= 60:
        raise CanaryError("timeout outside 1..60 seconds")
    data = json.dumps({
        "model": model, "messages": [{"role": "user", "content": CANARY_PROMPT}],
        "max_tokens": 256, "temperature": 0, "stream": False,
    }).encode("utf-8")
    req = Request(endpoint, data=data, method="POST",
                  headers={"Content-Type": "application/json",
                           "Accept": "application/json"})
    # Explicitly disable environment proxies and HTTP redirects. Otherwise a
    # local server can forward a prompt to an arbitrary Internet endpoint.
    client = opener if opener is not None else build_opener(ProxyHandler({}), _NoRedirect())
    begin = time.monotonic_ns()
    try:
        with client.open(req, timeout=timeout) as response:
            if response.status != 200:
                raise CanaryError("non-200 model response")
            if not response.headers.get("Content-Type", "").lower().startswith("application/json"):
                raise CanaryError("model response is not JSON content-type")
            raw = response.read(MAX_REPLY_BYTES + 1)
            if len(raw) > MAX_REPLY_BYTES:
                raise CanaryError("model response too large")
    except HTTPError as exc:
        raise CanaryError("model endpoint returned HTTP " + str(exc.code)) from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise CanaryError("local model endpoint unavailable") from exc
    end = time.monotonic_ns()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CanaryError("malformed model JSON response") from exc
    if not isinstance(payload, dict):
        raise CanaryError("model response must be a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise CanaryError("missing choices in model response")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str) or not message["content"].strip():
        raise CanaryError("missing generated text")
    usage = payload.get("usage")
    if usage is None:
        usage = {}
    if not isinstance(usage, dict):
        raise CanaryError("invalid usage object")
    output_tokens = _tokens(usage.get("completion_tokens"), "completion")
    input_tokens = _tokens(usage.get("prompt_tokens"), "prompt")
    return {
        "success": True, "endpoint": endpoint,
        "reported_model": str(payload.get("model", ""))[:128],
        "start_ns": begin, "end_ns": end,
        "latency_ms": round((end - begin) / 1000000, 2),
        "prompt_tokens_reported": input_tokens,
        "completion_tokens_reported": output_tokens,
        "content_logged": False,
    }


def peak_overlap(events):
    """Count overlapping HTTP response intervals, not GPU compute intervals."""
    points = []
    for item in events:
        if item.get("success") and type(item.get("start_ns")) is int and type(item.get("end_ns")) is int:
            if item["end_ns"] < item["start_ns"]:
                raise CanaryError("invalid request interval")
            points.extend(((item["start_ns"], 1), (item["end_ns"], -1)))
    active = peak = 0
    for _, change in sorted(points, key=lambda x: (x[0], x[1])):
        active += change
        peak = max(peak, active)
    return peak


def run_canary(endpoints, *, model="gpt-oss-20b", samples=1, parallel=1,
               timeout=30, caller=call_once):
    if (not isinstance(endpoints, list) or not 1 <= len(endpoints) <= MAX_ENDPOINTS
            or len(set(endpoints)) != len(endpoints)):
        raise CanaryError("requires 1..8 unique loopback endpoints")
    for endpoint in endpoints:
        local_endpoint(endpoint)
    if type(samples) is not int or not 1 <= samples <= MAX_SAMPLES:
        raise CanaryError("samples must be 1..64")
    if type(parallel) is not int or not 1 <= parallel <= MAX_PARALLEL:
        raise CanaryError("parallel must be 1..16")
    if len(endpoints) * samples > MAX_SAMPLES:
        raise CanaryError("maximum 64 total requests")
    if not isinstance(model, str) or not 1 <= len(model) <= 128:
        raise CanaryError("model identifier invalid")
    if type(timeout) not in (int, float) or not 1 <= timeout <= 60:
        raise CanaryError("timeout outside 1..60 seconds")

    def perform(endpoint):
        try:
            return caller(endpoint, model, timeout)
        except Exception as exc:
            return {"success": False, "endpoint": endpoint,
                    "error": type(exc).__name__ + ": " + str(exc)[:170],
                    "content_logged": False}

    jobs = [endpoint for _ in range(samples) for endpoint in endpoints]
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        results = list(pool.map(perform, jobs))
    successes = [r for r in results if r.get("success")]
    return {
        "schema": "mojum.gpu-legion-canary.v1",
        "attempts": len(jobs), "successful_responses": len(successes),
        "failed_responses": len(jobs) - len(successes),
        "peak_overlapping_http_calls": peak_overlap(successes),
        "total_completion_tokens_reported": sum(r.get("completion_tokens_reported") or 0 for r in successes),
        "results": results,
        "gpu_compute_concurrency_verified": False,
        "model_artifact_verified": False,
        "model_identity_attested": False,
        "deployment_performed": False,
        "alt_modified": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", action="append", required=True,
                        help="explicit http://127.0.0.1:PORT/v1/chat/completions")
    parser.add_argument("--model", default="gpt-oss-20b")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        report = run_canary(args.endpoint, model=args.model, samples=args.samples,
                            parallel=args.parallel, timeout=args.timeout)
    except CanaryError as exc:
        print(json.dumps({"schema": "mojum.gpu-legion-canary.v1",
                          "error": str(exc), "deployment_performed": False}), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["failed_responses"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
