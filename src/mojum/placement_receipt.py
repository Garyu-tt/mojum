"""Offline, read-only receipt for an independently safety-checked stateless placement.

A SHA-256 receipt binds a *declared* resource/trust snapshot and an exact plan
to a caller-supplied logical epoch. It is NOT a signature, lease, enrollment
proof, resource measurement, or permission to execute anything.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re

from .placement import (MAX_JOBS, MAX_NODES, NodeBudget, PlacementError,
                        PlacementPlan, StatelessJob)
from .placement_verify import verify_placement

SCHEMA = "mojum.mesh-placement-receipt/1"
MAX_EPOCH = 2**63 - 1
MAX_WIRE_BYTES = 512
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_FIELDS = frozenset({"schema", "epoch", "inputs_sha256", "plan_sha256", "receipt_sha256"})


@dataclass(frozen=True)
class PlacementReceipt:
    schema: str
    epoch: int
    inputs_sha256: str
    plan_sha256: str
    receipt_sha256: str

    def __post_init__(self):
        if type(self.schema) is not str or self.schema != SCHEMA:
            raise PlacementError("unsupported placement receipt schema")
        if type(self.epoch) is not int or not 0 <= self.epoch <= MAX_EPOCH:
            raise PlacementError("invalid placement epoch")
        if any(type(value) is not str or _SHA256.fullmatch(value) is None
               for value in (self.inputs_sha256, self.plan_sha256, self.receipt_sha256)):
            raise PlacementError("invalid receipt hash")


def _hash(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _receipt_hash(epoch: int, inputs_sha256: str, plan_sha256: str) -> str:
    return _hash({"domain": SCHEMA, "epoch": epoch,
                  "inputs_sha256": inputs_sha256, "plan_sha256": plan_sha256})


def _snapshots(candidate, jobs, nodes, *, local_node_id, trusted_remote_ids):
    # Reconstruct immutable records so even a forged dataclass instance cannot
    # skip the constructor's budget/capability checks. No resources are probed.
    if (type(jobs) not in (tuple, list) or len(jobs) > MAX_JOBS
            or type(nodes) not in (tuple, list)
            or not 1 <= len(nodes) <= MAX_NODES):
        raise PlacementError("invalid or oversized placement snapshot")
    if any(type(job) is not StatelessJob for job in jobs):
        raise PlacementError("invalid job snapshot")
    if any(type(node) is not NodeBudget for node in nodes):
        raise PlacementError("invalid node snapshot")
    safe_jobs = tuple(StatelessJob(j.job_id, j.cpu_slots, j.memory_mib,
                                   j.required_capabilities, j.priority,
                                   j.stateless) for j in jobs)
    safe_nodes = tuple(NodeBudget(n.node_id, n.cpu_slots, n.memory_mib,
                                  n.capabilities, n.online) for n in nodes)
    verify_placement(candidate, safe_jobs, safe_nodes,
                     local_node_id=local_node_id,
                     trusted_remote_ids=trusted_remote_ids)
    inputs = {
        "local_node_id": local_node_id,
        "trusted_remote_ids": sorted(trusted_remote_ids),
        "jobs": sorted((
            {"job_id": j.job_id, "cpu_slots": j.cpu_slots,
             "memory_mib": j.memory_mib,
             "required_capabilities": sorted(j.required_capabilities),
             "priority": j.priority, "stateless": j.stateless}
            for j in safe_jobs), key=lambda x: x["job_id"]),
        "nodes": sorted((
            {"node_id": n.node_id, "cpu_slots": n.cpu_slots,
             "memory_mib": n.memory_mib, "capabilities": sorted(n.capabilities),
             "online": n.online}
            for n in safe_nodes), key=lambda x: x["node_id"]),
    }
    proposed = {
        "assignments": sorted((
            {"job_id": a.job_id, "node_id": a.node_id}
            for a in candidate.assignments), key=lambda x: x["job_id"]),
        "deferred": sorted(candidate.deferred),
    }
    return inputs, proposed


def seal_placement(candidate: PlacementPlan, jobs, nodes, *, local_node_id: str,
                   trusted_remote_ids: frozenset[str] = frozenset(),
                   epoch: int) -> PlacementReceipt:
    """Bind a valid advisory plan to an exact declared snapshot and epoch."""
    if type(epoch) is not int or not 0 <= epoch <= MAX_EPOCH:
        raise PlacementError("invalid placement epoch")
    inputs, proposed = _snapshots(candidate, jobs, nodes,
                                   local_node_id=local_node_id,
                                   trusted_remote_ids=trusted_remote_ids)
    inputs_hash, plan_hash = _hash(inputs), _hash(proposed)
    return PlacementReceipt(SCHEMA, epoch, inputs_hash, plan_hash,
                            _receipt_hash(epoch, inputs_hash, plan_hash))


def verify_receipt(receipt: PlacementReceipt, candidate: PlacementPlan, jobs, nodes,
                   *, local_node_id: str,
                   trusted_remote_ids: frozenset[str] = frozenset(),
                   expected_epoch: int) -> bool:
    """Fail closed if the epoch, admitted inputs, or safe plan have changed.

    An independently authenticated channel must supply the trusted context.
    A sender can forge unkeyed hashes; this does NOT establish identity.
    """
    if type(receipt) is not PlacementReceipt:
        raise PlacementError("invalid receipt")
    current = seal_placement(candidate, jobs, nodes, local_node_id=local_node_id,
                             trusted_remote_ids=trusted_remote_ids,
                             epoch=expected_epoch)
    if not hmac.compare_digest(receipt_bytes(receipt), receipt_bytes(current)):
        raise PlacementError("stale, altered or mismatched placement receipt")
    return True


def receipt_bytes(receipt: PlacementReceipt) -> bytes:
    """Canonical portable JSON; does not serialize any PRIVATE job payload."""
    if type(receipt) is not PlacementReceipt:
        raise PlacementError("invalid receipt")
    # Validate the five raw scalar fields BEFORE attempting any copy or
    # serialization. dataclasses.asdict() deepcopies untrusted field objects;
    # forged constructor-bypassing instances must fail with PlacementError
    # without executing arbitrary __deepcopy__ methods.
    try:
        valid = PlacementReceipt(
            receipt.schema, receipt.epoch, receipt.inputs_sha256,
            receipt.plan_sha256, receipt.receipt_sha256,
        )
    except AttributeError as exc:
        raise PlacementError("invalid receipt fields") from exc
    fields = {
        "schema": valid.schema, "epoch": valid.epoch,
        "inputs_sha256": valid.inputs_sha256, "plan_sha256": valid.plan_sha256,
        "receipt_sha256": valid.receipt_sha256,
    }
    return json.dumps(fields, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def parse_receipt(raw: bytes) -> PlacementReceipt:
    """Strict bounded JSON reader, rejecting duplicate keys and unknown fields."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_WIRE_BYTES:
        raise PlacementError("invalid placement receipt size")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PlacementError("duplicate placement receipt key")
            result[key] = value
        return result
    def reject_constant(_):
        raise PlacementError("invalid placement receipt constant")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=reject_constant)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise PlacementError("malformed placement receipt JSON") from exc
    if type(data) is not dict or set(data) != _FIELDS:
        raise PlacementError("invalid placement receipt fields")
    return PlacementReceipt(**data)
