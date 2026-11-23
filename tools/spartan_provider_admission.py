"""Opt-in, CPU-first provider admission gate for existing Spartan llama.cpp endpoints.

No scheduler, model loader, credentials, prompt persistence, patch execution,
PR publication or implicit remote LiteLLM routes. Probe and call transport can
be replaced with offline fakes. This module never touches canonical journals.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from threading import BoundedSemaphore, Lock
from weakref import WeakValueDictionary
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SCHEMA = "mojum.spartan.provider-admission/1"
# Approved existing Spartan ports plus the optional, explicitly selected local
# LiteLLM proxy port. Do not accept URLs, hostnames or dynamic IP addresses.
LLAMA_PORTS = frozenset((18180, 18181, 18182, 18183, 48081, 48082, 48083, 48084))
LITELLM_PORT = 4000
MAX_JSON_BYTES = 65536
MAX_PROMPT_BYTES = 16384
MODEL_NAME = re.compile(r"[A-Za-z0-9_./:@+-]{1,128}\Z")


class AdmissionError(ValueError):
    """A missing proof or invalid input must fail closed, without secrets."""


class TransportError(AdmissionError):
    """Sanitized local transport failure; a call may already have been billed."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise AdmissionError("provider redirect prohibited")


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise AdmissionError("duplicate backend JSON key")
        result[name] = value
    return result


def _decode_json(raw):
    if len(raw) > MAX_JSON_BYTES:
        raise AdmissionError("oversized provider JSON")
    try:
        return json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              AdmissionError("nonfinite provider JSON")))
    except AdmissionError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AdmissionError("invalid provider JSON") from exc


def local_http(port: int, path: str, body=None, timeout: int = 5):
    """Only fixed loopback ports and API paths, no proxies or redirects."""
    if type(port) is not int or port not in LLAMA_PORTS | {LITELLM_PORT}:
        raise AdmissionError("unapproved local port")
    if type(path) is not str or path not in ("/health", "/slots", "/v1/models", "/v1/chat/completions"):
        raise AdmissionError("unapproved API path")
    if (body is None) != (path != "/v1/chat/completions"):
        raise AdmissionError("invalid request method")
    if type(timeout) is not int or not 1 <= timeout <= 90:
        raise AdmissionError("timeout exceeds admitted bounds")
    binary = None
    if body is not None:
        try:
            binary = json.dumps(body, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise AdmissionError("invalid outbound request") from exc
        if len(binary) > MAX_PROMPT_BYTES:
            raise AdmissionError("request byte budget exceeded")
    req = Request(f"http://127.0.0.1:{port}{path}", data=binary,
                  method="GET" if binary is None else "POST",
                  headers={"Accept": "application/json", "Content-Type": "application/json"})
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as response:
            if response.status != 200:
                raise TransportError("provider returned non-200 status")
            if not response.headers.get("Content-Type", "").lower().startswith("application/json"):
                raise AdmissionError("provider response is not JSON")
            return _decode_json(response.read(MAX_JSON_BYTES + 1))
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        # Do not include backend response bodies, URLs or backend error text.
        raise TransportError("local provider unavailable: " + type(exc).__name__) from exc


def _integer(number, name, *, low=0, high=2**31 - 1):
    if type(number) is not int or not low <= number <= high:
        raise AdmissionError("invalid " + name)
    return number


@dataclass(frozen=True)
class Provider:
    kind: str                       # llama_cpp or litellm; only llama is executable
    port: int
    model_id: str                   # exact value from /v1/models
    max_concurrent_calls: int       # max in THIS process, not fleet-wide
    max_completion_tokens: int
    min_context_tokens: int
    ram_per_call_mib: int           # measured operator requirement (KV/buffer headroom)
    vram_per_call_mib: int
    price_input_micro_usd_per_1k: int = 0
    price_output_micro_usd_per_1k: int = 0

    def __post_init__(self):
        if type(self.kind) is not str or self.kind not in ("llama_cpp", "litellm"):
            raise AdmissionError("unknown provider kind")
        allowed = LLAMA_PORTS if self.kind == "llama_cpp" else {LITELLM_PORT}
        if type(self.port) is not int or self.port not in allowed:
            raise AdmissionError("provider port not allowlisted")
        if (type(self.model_id) is not str or not MODEL_NAME.fullmatch(self.model_id)
                or "://" in self.model_id or self.model_id.startswith(("/", "."))
                or ".." in self.model_id):
            raise AdmissionError("invalid pinned model identity")
        _integer(self.max_concurrent_calls, "parallel limit", low=1, high=16)
        _integer(self.max_completion_tokens, "completion cap", low=1, high=4096)
        _integer(self.min_context_tokens, "minimum context", low=256, high=131072)
        _integer(self.ram_per_call_mib, "per-call RAM requirement", low=1, high=131072)
        _integer(self.vram_per_call_mib, "per-call VRAM requirement", high=131072)
        _integer(self.price_input_micro_usd_per_1k, "input price", high=10**9)
        _integer(self.price_output_micro_usd_per_1k, "output price", high=10**9)


@dataclass(frozen=True)
class Observation:
    model_id: str
    available_slots: int | None
    min_context_tokens: int | None
    slot_proof: str   # 'live-llama-slots' or 'unverified-proxy'


def _models(data):
    if type(data) is not dict or type(data.get("data")) is not list:
        raise AdmissionError("invalid model catalog")
    models = data["data"]
    if not 1 <= len(models) <= 128:
        raise AdmissionError("invalid model catalog size")
    ids = []
    for row in models:
        if type(row) is not dict or type(row.get("id")) is not str:
            raise AdmissionError("invalid advertised model identity")
        identifier = row["id"]
        if (not MODEL_NAME.fullmatch(identifier) or "://" in identifier
                or identifier.startswith(("/", ".")) or ".." in identifier):
            raise AdmissionError("invalid advertised model identifier")
        if identifier in ids:
            raise AdmissionError("duplicate advertised model identity")
        ids.append(identifier)
    return ids


def probe(provider: Provider, *, request=local_http) -> Observation:
    """Read actual local discovery; unknown LiteLLM capacity is *not* a slot."""
    ids = _models(request(provider.port, "/v1/models"))
    if provider.model_id not in ids:
        raise AdmissionError("pinned model not advertised by endpoint")
    if provider.kind == "litellm":
        # /v1/models alone does not prove routing to a LOCAL model, no remote
        # egress or any physical capacity. Never silently admit paid providers.
        return Observation(provider.model_id, None, None, "unverified-proxy")
    health = request(provider.port, "/health")
    if type(health) is not dict or health.get("status") != "ok":
        raise AdmissionError("local llama.cpp not ready")
    slots = request(provider.port, "/slots")
    if type(slots) is not list or not 1 <= len(slots) <= 16:
        raise AdmissionError("invalid llama.cpp slots")
    seen = set()
    contexts = []
    free = 0
    for row in slots:
        if type(row) is not dict:
            raise AdmissionError("invalid llama.cpp slot entry")
        sid = _integer(row.get("id"), "slot ID", high=10**9)
        if sid in seen:
            raise AdmissionError("duplicate llama.cpp slot")
        seen.add(sid)
        if type(row.get("is_processing")) is not bool:
            raise AdmissionError("missing authoritative slot busy flag")
        context = _integer(row.get("n_ctx"), "slot context", low=256, high=131072)
        contexts.append(context)
        free += not row["is_processing"]
    minimum = min(contexts)
    if minimum < provider.min_context_tokens:
        raise AdmissionError("endpoint context smaller than configured minimum")
    return Observation(provider.model_id, free, minimum, "live-llama-slots")


@dataclass(frozen=True)
class Headroom:
    """Explicit operator-supplied headroom. Not claimed to be auto-measured."""
    ram_mib: int
    vram_mib: int
    max_cost_micro_usd: int

    def __post_init__(self):
        _integer(self.ram_mib, "RAM headroom", high=2**31 - 1)
        _integer(self.vram_mib, "VRAM headroom", high=2**31 - 1)
        _integer(self.max_cost_micro_usd, "cost allowance", high=2**31 - 1)


@dataclass(frozen=True)
class Permit:
    schema: str
    provider_kind: str
    model_id: str
    admitted_calls: int
    reserved_token_budget: int
    estimated_cost_micro_usd: int
    proof: str


def admit(provider: Provider, observation: Observation, *, calls: int,
          prompt_tokens_cap: int, completion_tokens_cap: int,
          headroom: Headroom) -> Permit:
    """Fail closed: no partial admission, no unknown LiteLLM route proof."""
    _integer(calls, "requested calls", low=1, high=300)
    _integer(prompt_tokens_cap, "prompt token ceiling", low=1, high=131072)
    _integer(completion_tokens_cap, "output token ceiling", low=1, high=4096)
    if (type(observation) is not Observation
            or type(observation.available_slots) is not int
            or not 0 <= observation.available_slots <= 16
            or type(observation.min_context_tokens) is not int
            or not 256 <= observation.min_context_tokens <= 131072
            or observation.model_id != provider.model_id
            or observation.slot_proof != "live-llama-slots"
            or provider.kind != "llama_cpp"):
        raise AdmissionError("no verified local llama.cpp slot proof")
    if observation.min_context_tokens is None or (
            prompt_tokens_cap + completion_tokens_cap > observation.min_context_tokens):
        raise AdmissionError("requested context exceeds measured slot context")
    if completion_tokens_cap > provider.max_completion_tokens:
        raise AdmissionError("output tokens above provider cap")
    if (observation.available_slots is None
            or observation.available_slots < calls
            or calls > provider.max_concurrent_calls):
        raise AdmissionError("insufficient measured physical free slots")
    if headroom.ram_mib < calls * provider.ram_per_call_mib:
        raise AdmissionError("RAM headroom insufficient")
    if headroom.vram_mib < calls * provider.vram_per_call_mib:
        raise AdmissionError("VRAM headroom insufficient")
    per_call_usd = (prompt_tokens_cap * provider.price_input_micro_usd_per_1k
                    + completion_tokens_cap * provider.price_output_micro_usd_per_1k + 999) // 1000
    cost = calls * per_call_usd
    if cost > headroom.max_cost_micro_usd:
        raise AdmissionError("configured provider cost allowance exceeded")
    return Permit(SCHEMA, provider.kind, provider.model_id, calls,
                  calls * (prompt_tokens_cap + completion_tokens_cap),
                  cost, observation.slot_proof)


@dataclass
class _ProcessGate:
    limit: int
    semaphore: BoundedSemaphore


# A local server's slots are shared by every Session using the same port.
# Weak values release unused gates; a lock serializes concurrent construction.
_PROCESS_GATES = WeakValueDictionary()
_PROCESS_GATES_LOCK = Lock()


class Session:
    """Endpoint-wide in-process gate; Cezar still owns global lease fencing."""

    def __init__(self, provider: Provider, *, request=local_http):
        if type(provider) is not Provider:
            raise AdmissionError("validated provider required")
        self.provider = provider
        self._request = request
        with _PROCESS_GATES_LOCK:
            gate = _PROCESS_GATES.get(provider.port)
            if gate is None:
                gate = _ProcessGate(provider.max_concurrent_calls,
                                    BoundedSemaphore(provider.max_concurrent_calls))
                _PROCESS_GATES[provider.port] = gate
            elif gate.limit != provider.max_concurrent_calls:
                raise AdmissionError("conflicting per-endpoint parallel-call cap")
        self._gate = gate  # Keep the weak-registry entry alive for this Session.
        self._semaphore = gate.semaphore

    def complete(self, prompt: str, *, prompt_tokens_cap: int,
                 completion_tokens_cap: int, headroom: Headroom) -> str:
        if type(prompt) is not str or not prompt.strip() or len(prompt) > 8192:
            raise AdmissionError("prompt missing or exceeds 8192 characters")
        prompt_bytes = len(prompt.encode("utf-8"))
        if prompt_bytes > 12000:
            raise AdmissionError("prompt byte ceiling exceeded")
        # Cannot trust a caller-supplied estimate lower than a conservative
        # UTF-8-byte upper bound plus chat-template overhead.
        if prompt_tokens_cap < prompt_bytes + 32:
            raise AdmissionError("prompt token ceiling cannot cover raw input")
        if not self._semaphore.acquire(blocking=False):
            raise AdmissionError("local process parallel-call cap reached")
        try:
            obs = probe(self.provider, request=self._request)
            admit(self.provider, obs, calls=1, prompt_tokens_cap=prompt_tokens_cap,
                  completion_tokens_cap=completion_tokens_cap, headroom=headroom)
            payload = {"model": self.provider.model_id,
                       "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": completion_tokens_cap,
                       "temperature": 0, "stream": False}
            data = self._request(self.provider.port, "/v1/chat/completions",
                                 body=payload, timeout=90)
            if type(data) is not dict or data.get("model") != self.provider.model_id:
                raise AdmissionError("completion model identity mismatch")
            choices = data.get("choices")
            if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
                raise AdmissionError("invalid completion choices")
            choice = choices[0]
            if choice.get("finish_reason") != "stop":
                raise AdmissionError("incomplete or tool-directed completion")
            message = choice.get("message")
            if type(message) is not dict or type(message.get("content")) is not str:
                raise AdmissionError("invalid completion text")
            if message.get("tool_calls") is not None or message.get("function_call") is not None:
                raise AdmissionError("tool/function calls are prohibited")
            output = message["content"]
            if not output.strip() or len(output.encode("utf-8")) > MAX_JSON_BYTES:
                raise AdmissionError("empty or oversized completion")
            usage = data.get("usage")
            if usage is not None:
                if type(usage) is not dict:
                    raise AdmissionError("malformed reported usage")
                if "prompt_tokens" in usage:
                    used_input = _integer(usage["prompt_tokens"], "prompt usage", high=131072)
                    if used_input > prompt_tokens_cap:
                        raise AdmissionError("provider exceeded max prompt tokens")
                if "completion_tokens" in usage:
                    actual = _integer(usage["completion_tokens"], "usage", high=131072)
                    if actual > completion_tokens_cap:
                        raise AdmissionError("provider exceeded max completion tokens")
            return output
        finally:
            self._semaphore.release()
