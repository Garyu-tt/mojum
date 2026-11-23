"""Strict offline source-rig to stable Mojum joint-ID mapping."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

SCHEMA = "mojum.rig-joint-map/1"
_SOURCE_KEYS = frozenset({"rig_id", "revision", "joints"})
_TARGET_KEYS = frozenset({"rig_id", "revision", "joint_ids"})
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)
_MAX_JOINTS = 256
_MAX_ALIASES = 256
_MAX_TEXT = 96


class JointMapError(ValueError):
    """Invalid or ambiguous source-to-canonical rig mapping."""


@dataclass(frozen=True, slots=True)
class JointBinding:
    joint_id: str
    source_name: str
    via_alias: bool = False


@dataclass(frozen=True, slots=True)
class JointMap:
    """Immutable detached mapping receipt. Digest is not authentication."""
    schema: str
    source_rig_id: str
    source_revision: str
    target_rig_id: str
    target_revision: str
    bindings: tuple[JointBinding, ...]
    digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source": {"rig_id": self.source_rig_id, "revision": self.source_revision},
            "target": {"rig_id": self.target_rig_id, "revision": self.target_revision},
            "bindings": [
                {"joint_id": b.joint_id, "source_name": b.source_name, "via_alias": b.via_alias}
                for b in self.bindings
            ],
            "digest": self.digest,
        }


def _text(value: object, path: str, *, identifier: bool = False) -> str:
    if type(value) is not str or not value or len(value) > _MAX_TEXT:
        raise JointMapError(f"{path} must be a nonempty string of at most {_MAX_TEXT} chars")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise JointMapError(f"{path} must be valid UTF-8") from exc
    if identifier and _ID.fullmatch(value) is None:
        raise JointMapError(f"{path} is not a valid stable identifier")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise JointMapError(f"{path} contains control characters")
    return value


def _dict(value: object, expected: frozenset[str], path: str) -> dict:
    if type(value) is not dict:
        raise JointMapError(f"{path} must be a plain dict")
    keys = tuple(value.keys())
    if any(type(key) is not str for key in keys):
        raise JointMapError(f"{path} keys must be exact strings")
    if len(keys) != len(expected) or set(keys) != expected:
        raise JointMapError(f"{path} has missing or unknown fields")
    return value


def _inventory(value: object, path: str, *, ids: bool) -> tuple[str, ...]:
    if type(value) is not list or not 1 <= len(value) <= _MAX_JOINTS:
        raise JointMapError(f"{path} must be a nonempty list of at most {_MAX_JOINTS} entries")
    names = tuple(_text(v, f"{path}[{i}]", identifier=ids) for i, v in enumerate(value))
    if len(set(names)) != len(names):
        raise JointMapError(f"{path} contains duplicates")
    return names


def _descriptor(value: object, fields: frozenset[str], path: str):
    item = _dict(value, fields, path)
    rid = _text(item["rig_id"], path + ".rig_id", identifier=True)
    revision = _text(item["revision"], path + ".revision", identifier=True)
    key = "joints" if path == "source" else "joint_ids"
    inventory = _inventory(item[key], path + "." + key, ids=(path == "target"))
    return rid, revision, inventory


def _digest(payload: dict[str, Any]) -> str:
    try:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
        raise JointMapError("mapping is not canonical UTF-8 JSON") from exc
    return hashlib.sha256(raw).hexdigest()


def build_joint_map(source_rig: object, target_rig: object,
                    bindings: object, aliases: object | None = None) -> JointMap:
    """Map every target ID to a distinct exact source name.

    Descriptors are plain dicts: source={rig_id,revision,joints};
    target={rig_id,revision,joint_ids}. Bindings map target ID to source name.
    Optional aliases map alternate spelling to an exact source inventory name.
    Extra source joints are permitted; fuzzy matching and alias chaining are not.
    """
    src_id, src_rev, src_names = _descriptor(source_rig, _SOURCE_KEYS, "source")
    dst_id, dst_rev, dst_ids = _descriptor(target_rig, _TARGET_KEYS, "target")
    if type(bindings) is not dict:
        raise JointMapError("bindings must be a plain dict")
    bkeys = tuple(bindings.keys())
    if any(type(k) is not str for k in bkeys):
        raise JointMapError("binding target IDs must be exact strings")
    if len(bkeys) != len(dst_ids) or set(bkeys) != set(dst_ids):
        raise JointMapError("bindings must cover every target joint ID exactly")

    alias_map: dict[str, str] = {}
    if aliases is not None:
        if type(aliases) is not dict or len(aliases) > _MAX_ALIASES:
            raise JointMapError("aliases must be a plain dict within the alias budget")
        akeys = tuple(aliases.keys())
        if any(type(k) is not str for k in akeys):
            raise JointMapError("alias keys must be exact strings")
        src_set = set(src_names)
        for alias, canonical in aliases.items():
            alias = _text(alias, "alias key")
            canonical = _text(canonical, "alias target")
            if alias in src_set or alias == canonical or canonical not in src_set:
                raise JointMapError("alias must be a new spelling of an existing source name")
            alias_map[alias] = canonical

    src_set = set(src_names)
    used: set[str] = set()
    result: list[JointBinding] = []
    for joint_id in dst_ids:
        spelling = _text(bindings[joint_id], "binding for " + joint_id)
        via_alias = spelling in alias_map
        source_name = alias_map[spelling] if via_alias else spelling
        if source_name not in src_set:
            raise JointMapError("unknown source joint for " + joint_id)
        if source_name in used:
            raise JointMapError("one source joint cannot satisfy multiple target IDs")
        used.add(source_name)
        result.append(JointBinding(joint_id, source_name, via_alias))

    body = {
        "schema": SCHEMA,
        "source": {"rig_id": src_id, "revision": src_rev},
        "target": {"rig_id": dst_id, "revision": dst_rev},
        "bindings": [
            {"joint_id": b.joint_id, "source_name": b.source_name, "via_alias": b.via_alias}
            for b in result
        ],
    }
    return JointMap(SCHEMA, src_id, src_rev, dst_id, dst_rev, tuple(result), _digest(body))
