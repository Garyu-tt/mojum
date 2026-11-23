#!/usr/bin/env python3
"""Fail-closed composition of Spartan's durable budget ledger and a provider call.

The caller owns the SQLite connection and supplies a synchronous provider
callback plus a trusted usage-receipt extractor. No provider SDK or credentials
are imported here. A reservation is committed before the callback is invoked.
"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
import sqlite3
from typing import Callable

from tools import spartan_budget


class BudgetedCallError(ValueError):
    """The provider result could not be safely settled; reservation stays spent."""


def _is_async_callable(callback: Callable[..., object]) -> bool:
    """Identify async functions and callable instances with async __call__."""
    return (
        inspect.iscoroutinefunction(callback)
        or inspect.iscoroutinefunction(getattr(type(callback), "__call__", None))
    )


@dataclass(frozen=True)
class DispatchResult:
    """Metadata result; response is in-memory only and is never persisted here."""

    dispatched: bool
    settled: bool
    state: str
    actual_tokens: int | None = None
    actual_micro_usd: int | None = None
    response: object | None = None


def dispatch(
    db: sqlite3.Connection,
    *,
    request_id: str,
    worker: int,
    reserve_tokens: int,
    reserve_micro_usd: int,
    invoke: Callable[[], object],
    receipt_extractor: Callable[[object], dict],
) -> DispatchResult:
    """Reserve first, invoke at most once, then settle exact trusted usage.

    receipt_extractor is trusted dispatcher code, not model-generated code.
    It must return exactly {"actual_tokens": int, "actual_micro_usd": int}
    using native non-negative integers. If invocation or receipt extraction
    fails, the reservation remains debited. The caller must use a NEW request
    ID for a separately authorized retry.

    A duplicate request ID returns dispatched=False and never invokes the
    provider, even if the original request was previously settled.
    """
    if type(db) is not sqlite3.Connection:
        raise TypeError("db must be an exact sqlite3.Connection")
    if not callable(invoke) or not callable(receipt_extractor):
        raise TypeError("invoke and receipt_extractor must be callable")
    if _is_async_callable(invoke) or _is_async_callable(receipt_extractor):
        raise TypeError("invoke and receipt_extractor must be synchronous callables")

    reservation = spartan_budget.reserve(
        db, request_id, worker, reserve_tokens, reserve_micro_usd
    )
    if reservation["dispatch_allowed"] is not True:
        return DispatchResult(
            dispatched=False,
            settled=False,
            state=reservation["state"],
        )

    # This callback is the only provider invocation point. It is deliberately
    # outside any automatic retry loop: timeout ambiguity must not double-spend.
    response = invoke()
    if inspect.isawaitable(response):
        raise BudgetedCallError("provider callback must be synchronous")

    receipt = receipt_extractor(response)
    expected = {"actual_tokens", "actual_micro_usd"}
    if (type(receipt) is not dict or len(receipt) != 2
            or any(type(key) is not str for key in receipt)
            or set(receipt) != expected):
        raise BudgetedCallError("usage receipt has an invalid shape")
    actual_tokens = receipt["actual_tokens"]
    actual_micro_usd = receipt["actual_micro_usd"]
    if type(actual_tokens) is not int or actual_tokens < 0:
        raise BudgetedCallError("actual_tokens must be a native non-negative integer")
    if type(actual_micro_usd) is not int or actual_micro_usd < 0:
        raise BudgetedCallError("actual_micro_usd must be a native non-negative integer")

    spartan_budget.settle(
        db, request_id, worker, actual_tokens, actual_micro_usd
    )
    return DispatchResult(
        dispatched=True,
        settled=True,
        state="settled",
        actual_tokens=actual_tokens,
        actual_micro_usd=actual_micro_usd,
        response=response,
    )
