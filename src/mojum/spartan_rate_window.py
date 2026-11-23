"""Pure bounded synthetic model-provider request/token admission.

Deterministic preflight only, not an atomic distributed rate limiter.
"""
from dataclasses import dataclass

WINDOW_MS = 60_000
MAX_EVENTS = 10_000
MAX_TOKENS = 10_000_000


@dataclass(frozen=True, slots=True)
class RequestEvent:
    at_ms: int
    tokens: int


def admit_request(events: tuple[RequestEvent, ...], *, now_ms: int,
                  new_tokens: int, max_requests: int, max_tokens: int) -> tuple[bool, int]:
    """Return (allowed, earliest retry timestamp) using (now-60000, now].

    The caller must persist accepted events atomically across workers.
    No clock, model, secret or network is accessed.
    """
    def integer(value: object, lo: int, hi: int) -> int:
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError("invalid rate window input")
        return value

    integer(now_ms, 0, 2**63 - 1)
    integer(new_tokens, 1, MAX_TOKENS)
    integer(max_requests, 1, MAX_EVENTS)
    integer(max_tokens, 1, MAX_TOKENS)
    if type(events) is not tuple or len(events) > MAX_EVENTS:
        raise ValueError("invalid rate window input")
    last = -1
    active = []
    for event in events:
        if type(event) is not RequestEvent:
            raise ValueError("invalid rate window input")
        at = integer(event.at_ms, 0, now_ms)
        tok = integer(event.tokens, 1, MAX_TOKENS)
        if at < last:
            raise ValueError("invalid rate window input")
        last = at
        if at > now_ms - WINDOW_MS:
            active.append((at, tok))
    if new_tokens > max_tokens:
        return False, now_ms + WINDOW_MS
    if len(active) < max_requests and sum(t for _, t in active) + new_tokens <= max_tokens:
        return True, now_ms
    count = len(active)
    total = sum(t for _, t in active)
    for at, tok in active:
        count -= 1
        total -= tok
        if count < max_requests and total + new_tokens <= max_tokens:
            return False, at + WINDOW_MS
    return False, now_ms + WINDOW_MS
