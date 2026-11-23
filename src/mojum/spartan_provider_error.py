"""Secret-free deterministic classification of provider HTTP outcomes.

Only numeric status and retry budget are accepted. No bodies, URLs, headers,
exception strings, credentials or prompts enter this module.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderDecision:
    category: str
    retryable: bool
    retry_allowed: bool
    terminal: bool


def classify_http_status(status: int, attempt: int, max_attempts: int) -> ProviderDecision:
    """Classify a completed request; caller owns deadlines, budgets and backoff."""
    for name, value in (("status", status), ("attempt", attempt), ("max_attempts", max_attempts)):
        if type(value) is not int:
            raise ValueError(f"{name} must be an integer")
    if not 100 <= status <= 599:
        raise ValueError("status out of range")
    if not 1 <= max_attempts <= 16:
        raise ValueError("max_attempts out of range")
    if not 1 <= attempt <= max_attempts:
        raise ValueError("attempt out of range")
    if 200 <= status <= 299:
        category = "success"
    elif status in (408, 425, 429, 500, 502, 503, 504):
        category = "transient"
    elif status in (401, 403):
        category = "authorization"
    elif status == 402:
        category = "billing"
    elif 400 <= status <= 499:
        category = "request"
    elif 500 <= status <= 599:
        category = "server"
    else:
        category = "unexpected"
    retryable = category == "transient"
    retry_allowed = retryable and attempt < max_attempts
    return ProviderDecision(category, retryable, retry_allowed, not retry_allowed and category != "success")
