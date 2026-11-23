"""Offline bounded public usage receipts; not an authoritative billing ledger."""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\\Z", re.ASCII)
_MAX_MICROS = 999_999_999_999


@dataclass(frozen=True, slots=True)
class UsageReceipt:
    request_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    usd_micros: int
    fingerprint: str


def _id(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError("invalid public identifier")
    return value


def _tokens(value):
    if type(value) is not int or not 0 <= value <= 10_000_000:
        raise ValueError("invalid token count")
    return value


def _micros(value):
    if type(value) is not str or re.fullmatch(
        r"(?:0|[1-9][0-9]{0,5})(?:\\.[0-9]{1,6})?", value, re.ASCII
    ) is None:
        raise ValueError("invalid USD amount")
    return int(Decimal(value) * 1_000_000)


def make_usage_receipt(*, request_id, provider, model, input_tokens, output_tokens, usd):
    values = (
        _id(request_id), _id(provider), _id(model),
        _tokens(input_tokens), _tokens(output_tokens), _micros(usd),
    )
    payload = json.dumps(
        ["mojum.spartan.usage/1", *values],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return UsageReceipt(
        *values,
        hashlib.sha256(payload.encode("ascii")).hexdigest(),
    )


def _validate_receipt(item):
    """Validate native field types before formatting or equality operations."""
    if type(item) is not UsageReceipt:
        raise ValueError("invalid receipt")
    _id(item.request_id)
    _id(item.provider)
    _id(item.model)
    _tokens(item.input_tokens)
    _tokens(item.output_tokens)
    if type(item.usd_micros) is not int or not 0 <= item.usd_micros <= _MAX_MICROS:
        raise ValueError("usd_micros must be a native bounded integer")
    if type(item.fingerprint) is not str or _SHA256.fullmatch(item.fingerprint) is None:
        raise ValueError("fingerprint must be a lowercase SHA-256 hex digest")

    amount = (
        f"{item.usd_micros // 1_000_000}."
        f"{item.usd_micros % 1_000_000:06d}"
    )
    expected = make_usage_receipt(
        request_id=item.request_id,
        provider=item.provider,
        model=item.model,
        input_tokens=item.input_tokens,
        output_tokens=item.output_tokens,
        usd=amount,
    )
    if expected.fingerprint != item.fingerprint:
        raise ValueError("receipt fingerprint mismatch")


def within_budget(receipts, *, limit_usd):
    if type(receipts) is not tuple or len(receipts) > 10_000:
        raise ValueError("invalid receipt collection")
    total = 0
    for item in receipts:
        _validate_receipt(item)
        total += item.usd_micros
    return total <= _micros(limit_usd)
