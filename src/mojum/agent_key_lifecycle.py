"""Offline lifecycle policy for caller-managed external-agent HMAC keys."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, re
from typing import Iterable

_SCHEMA = "mojum.agent-keyring/1"
_PURPOSE = "agent-heartbeat"
_ALGORITHM = "HMAC-SHA256"
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", re.ASCII)
_MAX_KEYS = 64

class KeyLifecycleError(ValueError):
    """Invalid key metadata or lifecycle decision."""

@dataclass(frozen=True, slots=True)
class KeyMetadata:
    key_id: str
    created_at: int
    activates_at: int
    retires_at: int | None = None
    verify_until: int | None = None
    revoked_at: int | None = None
    purpose: str = _PURPOSE
    algorithm: str = _ALGORITHM

@dataclass(frozen=True, slots=True)
class Keyring:
    keys: tuple[KeyMetadata, ...]
    digest: str

def _time(value: object, name: str, optional: bool = False) -> int | None:
    if optional and value is None: return None
    if type(value) is not int or not 0 <= value <= 9_223_372_036_854_775_807:
        raise KeyLifecycleError(f"{name} must be a non-negative signed 64-bit integer")
    return value

def _record(item: object) -> KeyMetadata:
    if type(item) is not dict: raise KeyLifecycleError("key record must be an exact dict")
    allowed = {"key_id","created_at","activates_at","retires_at","verify_until","revoked_at","purpose","algorithm"}
    if set(item) - allowed or not {"key_id","created_at","activates_at"} <= set(item):
        raise KeyLifecycleError("missing or unknown key metadata fields")
    kid = item["key_id"]
    if type(kid) is not str or not _KEY_ID.fullmatch(kid): raise KeyLifecycleError("malformed key_id")
    purpose, algorithm = item.get("purpose",_PURPOSE), item.get("algorithm",_ALGORITHM)
    if type(purpose) is not str or purpose != _PURPOSE or type(algorithm) is not str or algorithm != _ALGORITHM:
        raise KeyLifecycleError("unsupported purpose or algorithm")
    created = _time(item["created_at"],"created_at")
    activates = _time(item["activates_at"],"activates_at")
    retires = _time(item.get("retires_at"),"retires_at",True)
    until = _time(item.get("verify_until"),"verify_until",True)
    revoked = _time(item.get("revoked_at"),"revoked_at",True)
    assert created is not None and activates is not None
    if activates < created: raise KeyLifecycleError("activation precedes creation")
    if retires is not None and retires <= activates: raise KeyLifecycleError("retirement must follow activation")
    if until is not None and (retires is None or until < retires): raise KeyLifecycleError("invalid verification retention")
    if revoked is not None and revoked < created: raise KeyLifecycleError("revocation precedes creation")
    return KeyMetadata(kid,created,activates,retires,until,revoked,purpose,algorithm)

def build_keyring(records: Iterable[dict]) -> Keyring:
    """Validate and freeze at most 64 records. Secret key bytes are never accepted."""
    if type(records) not in (list,tuple) or not 1 <= len(records) <= _MAX_KEYS:
        raise KeyLifecycleError("records must be a list/tuple containing 1..64 keys")
    keys = tuple(sorted((_record(x) for x in records),key=lambda x:x.key_id))
    if len({x.key_id for x in keys}) != len(keys): raise KeyLifecycleError("duplicate key_id")
    intervals = sorted((x.activates_at,x.retires_at or 9_223_372_036_854_775_807)
                       for x in keys if x.revoked_at is None)
    for left,right in zip(intervals,intervals[1:]):
        if right[0] < left[1]: raise KeyLifecycleError("non-revoked signing windows overlap")
    payload = {"schema":_SCHEMA,"keys":[
        {"key_id":x.key_id,"created_at":x.created_at,"activates_at":x.activates_at,
         "retires_at":x.retires_at,"verify_until":x.verify_until,"revoked_at":x.revoked_at,
         "purpose":x.purpose,"algorithm":x.algorithm} for x in keys]}
    raw = json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode("ascii")
    return Keyring(keys,hashlib.sha256(raw).hexdigest())

def signing_key_id(keyring: Keyring, now: int) -> str:
    now = _time(now,"now")
    active = [x.key_id for x in keyring.keys if x.revoked_at is None and x.activates_at <= now
              and (x.retires_at is None or now < x.retires_at)]
    if len(active) != 1: raise KeyLifecycleError("no unique active signing key")
    return active[0]

def verification_key_id(keyring: Keyring, key_id: str, event_time: int, now: int) -> str:
    event_time, now = _time(event_time,"event_time"), _time(now,"now")
    if type(key_id) is not str or not _KEY_ID.fullmatch(key_id): raise KeyLifecycleError("malformed key_id")
    match = next((x for x in keyring.keys if x.key_id == key_id),None)
    if match is None or match.revoked_at is not None: raise KeyLifecycleError("unknown or revoked key")
    if event_time < match.activates_at or (match.retires_at is not None and event_time >= match.retires_at):
        raise KeyLifecycleError("event outside key signing window")
    if match.verify_until is not None and now > match.verify_until:
        raise KeyLifecycleError("historical verification retention expired")
    if now < event_time: raise KeyLifecycleError("future event timestamp")
    return match.key_id
