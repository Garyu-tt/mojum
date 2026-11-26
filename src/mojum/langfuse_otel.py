"""Opt-in sanitized Langfuse OTLP/HTTP exporter for Spartan QA telemetry.

No agent prompt, source, exception text, organism memory, or arbitrary metadata
may cross this boundary. No imports, sockets or credentials are used by default.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID

SCHEMA = "mojum.langfuse.quality-event/1"
PHASES = {
    "provider_admission": frozenset({"admitted", "rejected"}),
    "provider_result": frozenset({"ok", "failed", "cancelled"}),
    "patch": frozenset({"ready", "failed"}),
    "ci": frozenset({"passed", "failed", "pending"}),
    "qa": frozenset({"accepted", "rejected", "blocked"}),
}
FIELDS = frozenset({"schema", "event_id", "lease_id", "attempt", "phase",
                    "outcome", "timestamp_ms", "pr_head", "pr_number",
                    "model_class", "input_tokens", "output_tokens",
                    "cost_microusd", "duration_ms"})
MEASURED = ("input_tokens", "output_tokens", "cost_microusd")
HEX_HEAD = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}\Z", re.ASCII)
MAX_INPUT_BYTES = 2048


class TelemetryError(ValueError):
    """A rejected privacy boundary or a failed optional export."""


def _number(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise TelemetryError(f"{name}: invalid integer")
    return value


def _uuid(value: object, name: str) -> UUID:
    if type(value) is not str or len(value) != 36:
        raise TelemetryError(f"{name}: UUID required")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise TelemetryError(f"{name}: UUID required") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise TelemetryError(f"{name}: canonical random UUIDv4 required")
    return parsed


def validate(event: dict) -> dict:
    """Construct a *new* allowlisted value; never recursively sanitize input.

    A field omitted in trusted accounting is unknown, not a measured zero.
    This contract intentionally has no prompt, model name, paths, or free text.
    """
    if type(event) is not dict or event.keys() - FIELDS:
        raise TelemetryError("unknown field or invalid event object")
    required = {"schema", "event_id", "lease_id", "attempt", "phase", "outcome", "timestamp_ms"}
    if not required <= event.keys() or type(event["schema"]) is not str or event["schema"] != SCHEMA:
        raise TelemetryError("event schema/required fields mismatch")
    event_id = _uuid(event["event_id"], "event_id")
    lease_id = _uuid(event["lease_id"], "lease_id")
    attempt = _number(event["attempt"], "attempt", 1, 1_000_000)
    phase = event["phase"]
    if type(phase) is not str or phase not in PHASES:
        raise TelemetryError("unknown event phase")
    outcome = event["outcome"]
    if type(outcome) is not str or outcome not in PHASES[phase]:
        raise TelemetryError("unknown event outcome")
    stamp = _number(event["timestamp_ms"], "timestamp_ms", 1_577_836_800_000, 4_102_444_800_000)
    safe = {"schema": SCHEMA, "event_id": str(event_id), "lease_id": str(lease_id),
            "attempt": attempt, "phase": phase, "outcome": outcome, "timestamp_ms": stamp}
    if phase in ("patch", "ci", "qa") and ("pr_head" not in event or "pr_number" not in event):
        raise TelemetryError("patch/CI/QA requires exact PR HEAD and number")
    if "pr_head" in event:
        head = event["pr_head"]
        if type(head) is not str or not HEX_HEAD.fullmatch(head):
            raise TelemetryError("pr_head must be lowercase exact commit SHA")
        safe["pr_head"] = head
    if "pr_number" in event:
        safe["pr_number"] = _number(event["pr_number"], "pr_number", 1, 10_000_000)
    if "model_class" in event:
        model_class = event["model_class"]
        if (phase not in ("provider_admission", "provider_result")
                or type(model_class) is not str
                or model_class not in ("cpu_local", "gpu_local", "remote")):
            raise TelemetryError("model_class: invalid or out of phase")
        safe["model_class"] = model_class
    for field in MEASURED:
        if field in event:
            if phase != "provider_result":
                raise TelemetryError("provider usage forbidden outside result")
            safe[field] = _number(event[field], field, 0, 10**12)
    if "duration_ms" in event:
        safe["duration_ms"] = _number(event["duration_ms"], "duration_ms", 0, 3_600_000)
    return safe


def _attr(key: str, value: str | int) -> dict:
    # All keys are constants selected by us, not caller-controlled.
    return {"key": key, "value": {"intValue" if type(value) is int else "stringValue": str(value)}}


def to_otlp(event: dict) -> dict:
    """Version-4 Langfuse-compatible JSON OTLP/HTTP, one immutable safe span."""
    safe = validate(event)
    lease = UUID(safe["lease_id"]).bytes
    token = lease + safe["attempt"].to_bytes(4, "big")
    trace_id = hashlib.sha256(b"mojum-langfuse-trace-v1:" + token).hexdigest()[:32]
    span_id = hashlib.sha256(b"mojum-langfuse-span-v1:" + token + UUID(safe["event_id"]).bytes).hexdigest()[:16]
    attrs = [_attr("mojum.schema", SCHEMA), _attr("mojum.phase", safe["phase"]),
             _attr("mojum.outcome", safe["outcome"]), _attr("mojum.attempt", safe["attempt"])]
    for field in ("pr_head", "pr_number", "model_class", *MEASURED, "duration_ms"):
        if field in safe:
            attrs.append(_attr("mojum." + field, safe[field]))
    start = safe["timestamp_ms"] * 1_000_000
    duration = max(1, safe.get("duration_ms", 1)) * 1_000_000
    return {"resourceSpans": [{"resource": {"attributes": [_attr("service.name", "mojum-spartan-qa")]},
            "scopeSpans": [{"scope": {"name": "mojum.langfuse.quality-event", "version": "1"},
            "spans": [{"traceId": trace_id, "spanId": span_id,
                       "name": "mojum." + safe["phase"],
                       "startTimeUnixNano": str(start), "endTimeUnixNano": str(start + duration),
                       "attributes": attrs}]}]}]}


def _url(host: str) -> str:
    if type(host) is not str or len(host) > 255:
        raise TelemetryError("invalid Langfuse host")
    try:
        url = urlsplit(host)
        port = url.port
    except ValueError as exc:
        raise TelemetryError("invalid Langfuse host") from exc
    if not url.hostname or port == 0 or url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
        raise TelemetryError("Langfuse host must be a bare origin")
    if url.scheme == "http":
        try:
            if not ipaddress.ip_address(url.hostname).is_loopback:
                raise TelemetryError("HTTP is permitted only for numeric loopback")
        except ValueError as exc:
            raise TelemetryError("HTTP is permitted only for numeric loopback") from exc
    elif url.scheme != "https":
        raise TelemetryError("HTTPS required for remote Langfuse")
    return host.rstrip("/") + "/api/public/otel/v1/traces"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(event: dict, *, enabled: bool = False, host: str = "",
         public_key: str = "", secret_key: str = "", timeout: float = 3.0) -> bool:
    """Network-free unless explicitly enabled. Never log sensitive failures."""
    if not enabled:
        return False
    payload = json.dumps(to_otlp(event), separators=(",", ":"), allow_nan=False).encode("utf-8")
    endpoint = _url(host)
    if not public_key.startswith("pk-") or not secret_key.startswith("sk-"):
        raise TelemetryError("Langfuse credentials missing or malformed")
    if type(timeout) not in (float, int) or not 0 < timeout <= 10:
        raise TelemetryError("invalid Langfuse timeout")
    auth = base64.b64encode((public_key + ":" + secret_key).encode("utf-8")).decode("ascii")
    request = Request(endpoint, data=payload, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Basic " + auth,
        "x-langfuse-ingestion-version": "4"})
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status < 200 or response.status >= 300:
                raise TelemetryError("Langfuse rejected telemetry")
            data = response.read(4097)
        if len(data) > 4096:
            raise TelemetryError("Langfuse response too large")
        if data:
            result = json.loads(data)
            if type(result) is not dict or result.get("partialSuccess"):
                raise TelemetryError("Langfuse partial/invalid acknowledgment")
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise TelemetryError("Langfuse delivery failed (no retry)") from None
    return True


def summarize(events: list[dict]) -> dict:
    """Local-only counts; CI success without matching QA acceptance is not release."""
    if type(events) is not list or len(events) > 10_000:
        raise TelemetryError("summary event limit exceeded")
    safe = [validate(item) for item in events]
    counts = {phase: {outcome: 0 for outcome in sorted(outcomes)} for phase, outcomes in PHASES.items()}
    sums = {name: 0 for name in MEASURED}
    reports = {name: 0 for name in MEASURED}
    by_head = {}
    for e in safe:
        counts[e["phase"]][e["outcome"]] += 1
        for field in MEASURED:
            if field in e:
                sums[field] += e[field]
                reports[field] += 1
        if e["phase"] in ("ci", "qa"):
            key = (e["lease_id"], e["attempt"], e["pr_number"], e["pr_head"])
            by_head.setdefault(key, {"ci": set(), "qa": set()})[e["phase"]].add(e["outcome"])
    mismatches = sum(1 for v in by_head.values() if "passed" in v["ci"] and v["qa"] & {"rejected", "blocked"})
    return {"schema": "mojum.langfuse.quality-summary/1", "events": len(safe),
            "counts": counts, "reported_totals": sums, "reported_counts": reports,
            "ci_pass_qa_fail_exact_head": mismatches}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sanitized Langfuse OTLP event; no network by default")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--send", action="store_true", help="explicitly send to configured Langfuse host")
    action.add_argument("--summary", action="store_true", help="summarize a JSON array locally")
    parser.add_argument("--host", help="bare Langfuse origin, required for --send")
    args = parser.parse_args(argv)
    raw = sys.stdin.buffer.read(100_001 if args.summary else MAX_INPUT_BYTES + 1)
    if len(raw) > (100_000 if args.summary else MAX_INPUT_BYTES):
        parser.error("input exceeds limit")
    try:
        obj = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(TelemetryError("nonfinite JSON")))
        if args.summary:
            print(json.dumps(summarize(obj), sort_keys=True))
        elif args.send:
            send(obj, enabled=True, host=args.host or "",
                 public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
                 secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""))
            print("delivered")
        else:
            print(json.dumps(to_otlp(obj), sort_keys=True))
    except (TelemetryError, ValueError, TypeError) as exc:
        # Error messages are fixed and never interpolate incoming values.
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
