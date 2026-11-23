#!/usr/bin/env python3
"""Opt-in, credential-free loopback OpenAI-compatible provider for Spartan.

This is a single-process request adapter, NOT a scheduler or model launcher.
It requires the existing spartan_budget SQLite ledger and one fenced lease
validated by the caller. Failed/ambiguous calls retain their budget debit.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import re
from threading import BoundedSemaphore, Lock
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from tools import spartan_budget

_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z", re.ASCII)
_MAX_RESPONSE = 131072
_MAX_PROMPT_BYTES = 8192


class ProviderDenied(ValueError):
    """Fail-closed admission or provider response, with no secret-bearing errors."""


@dataclass(frozen=True)
class ProviderPolicy:
    provider: str  # llama_cpp or litellm_proxy, both already running on loopback
    model: str  # exact expected request AND response identity / LiteLLM alias
    port: int
    enabled: bool = False
    max_inflight: int = 1
    requests_per_minute: int = 8
    tokens_per_second: int = 64  # local process admission bound, not measured TPS
    max_output_tokens: int = 256
    timeout_seconds: int = 30
    retries: int = 0  # no ambiguous retransmission or double billing
    declared_ram_mib: int = 1024
    ram_ceiling_mib: int = 1024
    declared_vram_mib: int = 0
    vram_ceiling_mib: int = 0

    def __post_init__(self):
        if self.provider not in ("llama_cpp", "litellm_proxy"):
            raise ProviderDenied("unknown local provider kind")
        if type(self.model) is not str or not _MODEL.fullmatch(self.model):
            raise ProviderDenied("invalid model identity")
        for field, lower, upper in (
            ("port", 1, 65535), ("max_inflight", 1, 8),
            ("requests_per_minute", 1, 600), ("tokens_per_second", 1, 100000),
            ("max_output_tokens", 1, 4096), ("timeout_seconds", 1, 90),
            ("declared_ram_mib", 1, 1048576), ("ram_ceiling_mib", 1, 1048576),
            ("declared_vram_mib", 0, 1048576), ("vram_ceiling_mib", 0, 1048576),
        ):
            v = getattr(self, field)
            if type(v) is not int or not lower <= v <= upper:
                raise ProviderDenied("invalid provider policy: " + field)
        if type(self.enabled) is not bool or type(self.retries) is not int or self.retries != 0:
            raise ProviderDenied("provider must be explicitly enabled with zero retries")
        if (self.declared_ram_mib > self.ram_ceiling_mib
                or self.declared_vram_mib > self.vram_ceiling_mib):
            raise ProviderDenied("declared model memory exceeds operator ceiling")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _loopback_post(policy: ProviderPolicy, request_id: str, payload: dict) -> dict:
    """One request, fixed loopback IP/path, no proxy, redirects, auth or retry."""
    raw = json.dumps(payload, ensure_ascii=True, allow_nan=False,
                     separators=(",", ":")).encode("ascii")
    if len(raw) > 32768:
        raise ProviderDenied("provider request too large")
    req = Request(f"http://127.0.0.1:{policy.port}/v1/chat/completions", data=raw,
                  method="POST", headers={"Content-Type": "application/json",
                                           "Accept": "application/json",
                                           "X-Request-ID": request_id})
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(req, timeout=policy.timeout_seconds) as response:
            if response.status != 200 or not response.headers.get(
                    "Content-Type", "").lower().startswith("application/json"):
                raise ProviderDenied("unexpected provider HTTP response")
            data = response.read(_MAX_RESPONSE + 1)
    except (HTTPError, URLError, OSError, TimeoutError):
        raise ProviderDenied("local provider unavailable or rejected request") from None
    if len(data) > _MAX_RESPONSE:
        raise ProviderDenied("provider response too large")
    try:
        result = json.loads(data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise ProviderDenied("malformed provider JSON") from None
    if type(result) is not dict:
        raise ProviderDenied("provider JSON must be an object")
    return result


def _usage_and_text(body: dict, model: str) -> tuple[str, int, int]:
    if not isinstance(body, dict) or body.get("model") != model:
        raise ProviderDenied("provider model identity mismatch")
    usage = body.get("usage")
    if not isinstance(usage, dict):
        raise ProviderDenied("missing provider token usage")
    input_tokens, output_tokens, total_tokens = (
        usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens"))
    if any(type(n) is not int or n < 0 for n in
           (input_tokens, output_tokens, total_tokens)) or total_tokens != input_tokens + output_tokens:
        raise ProviderDenied("invalid provider token usage")
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ProviderDenied("expected exactly one provider choice")
    choice = choices[0]
    if (not isinstance(choice, dict) or choice.get("finish_reason") != "stop"
            or not isinstance(choice.get("message"), dict)
            or choice["message"].get("role") != "assistant"
            or type(choice["message"].get("content")) is not str
            or choice["message"].get("tool_calls") is not None):
        raise ProviderDenied("provider generated unsupported output")
    content = choice["message"]["content"]
    try:
        if len(content.encode("utf-8")) > 65536:
            raise ProviderDenied("provider text too large")
    except UnicodeError:
        raise ProviderDenied("invalid provider text encoding") from None
    return content, total_tokens, output_tokens


class LocalOpenAIProvider:
    """Process-local caps plus existing cross-process durable budget admission."""

    def __init__(self, policy: ProviderPolicy, *, transport=None, clock=monotonic):
        self.policy = policy
        self._transport = transport or _loopback_post
        self._clock = clock
        self._slots = BoundedSemaphore(policy.max_inflight)
        self._lock = Lock()
        self._window: deque[tuple[float, int]] = deque()

    def complete(self, *, db, worker: int, request_id: str, prompt: str) -> dict:
        """No automatic retries. Caller must separately validate lease fencing.

        Returns untrusted text and verified usage. Does not publish a patch/PR.
        db is a caller-owned, private spartan_budget connection, not shared
        between threads. No raw provider responses or prompts are logged.
        """
        policy = self.policy
        if not policy.enabled:
            raise ProviderDenied("provider not opted in")
        # A loopback LiteLLM proxy can route to billable cloud upstreams.
        # No trusted upstream routing attestation or USD settlement exists yet:
        # a zero-USD reservation would bypass the durable spending ceiling.
        if policy.provider == "litellm_proxy":
            raise ProviderDenied("LiteLLM proxy routing may be billable; USD admission not implemented")
        if type(prompt) is not str:
            raise ProviderDenied("prompt must be text")
        try:
            raw = prompt.encode("utf-8")
        except UnicodeError:
            raise ProviderDenied("invalid prompt encoding") from None
        if not raw or len(raw) > _MAX_PROMPT_BYTES:
            raise ProviderDenied("prompt byte bound exceeded")
        # A conservative upper bound: at most one token per UTF-8 byte.
        reserved = len(raw) + policy.max_output_tokens
        if not self._slots.acquire(blocking=False):
            raise ProviderDenied("process-local concurrency cap reached")
        try:
            now = self._clock()
            with self._lock:
                while self._window and now - self._window[0][0] >= 60:
                    self._window.popleft()
                if (len(self._window) >= policy.requests_per_minute
                        or sum(tokens for _, tokens in self._window) + reserved >
                        policy.tokens_per_second * 60):
                    raise ProviderDenied("process-local rate cap reached")
                # Never refund admission on failures or ambiguous timeouts.
                self._window.append((now, reserved))
            try:
                reservation = spartan_budget.reserve(db, request_id, worker, reserved, 0)
            except spartan_budget.BudgetError:
                raise ProviderDenied("durable provider budget denied request") from None
            if not reservation["dispatch_allowed"]:
                raise ProviderDenied("duplicate request; provider dispatch forbidden")
            payload = {"model": policy.model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": policy.max_output_tokens, "temperature": 0,
                       "stream": False, "n": 1}
            answer = self._transport(policy, request_id, payload)
            # Validate accounting independently of content, so a valid
            # overrun freezes the ledger even for truncated/invalid choices.
            if not isinstance(answer, dict) or answer.get("model") != policy.model:
                raise ProviderDenied("provider model identity mismatch")
            usage = answer.get("usage")
            if not isinstance(usage, dict):
                raise ProviderDenied("missing provider token usage")
            counts = [usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")]
            if (any(type(n) is not int or n < 0 for n in counts)
                    or counts[0] + counts[1] != counts[2]):
                raise ProviderDenied("invalid provider token usage")
            try:
                spartan_budget.settle(db, request_id, worker, counts[2], 0)
            except spartan_budget.BudgetError:
                raise ProviderDenied("durable budget frozen after provider overrun") from None
            text, _, output_tokens = _usage_and_text(answer, policy.model)
            if output_tokens > policy.max_output_tokens:
                raise ProviderDenied("provider exceeded generation limit")
            return {"request_id": request_id, "provider": policy.provider,
                    "model": policy.model, "text": text, "input_tokens": counts[0],
                    "output_tokens": counts[1], "reserved_tokens": reserved,
                    "usd_micro": 0, "verified_local_execution": False}
        finally:
            self._slots.release()
