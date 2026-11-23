"""Offline 3-node majority decision helper; not an election or consensus service.

The caller must authenticate vote receipts and durably enforce the returned
fencing epoch separately. This module only validates their bounded shape and
computes whether a strict majority supports one candidate.
"""
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import re

from .mesh_contract import _NODE_ID

SCHEMA = "mojum.mesh-vote/1"
MAX_MEMBERS = 3
MAX_VOTES = 3
MAX_SEEN_VOTE_IDS = 100_000
MAX_EPOCH = (1 << 63) - 1
MAX_TIMESTAMP_NS = (1 << 63) - 1
MAX_VOTE_TTL_NS = 300_000_000_000  # five minutes
_VOTE_ID = re.compile(r"[0-9a-f]{32}", re.ASCII)
_VOTE_FIELDS = frozenset({
    "schema", "organism_id", "voter_id", "candidate_id", "epoch",
    "issued_at_ns", "expires_at_ns", "vote_id",
})


class MeshQuorumError(ValueError):
    """Invalid or ambiguous quorum input; fail closed."""


@dataclass(frozen=True)
class QuorumDecision:
    """Digest-only result; it never grants writer authority by itself."""

    granted: bool
    candidate_id: str | None
    epoch: int
    votes_received: int
    support: int
    reason: str
    digest: str


def _node_id(value: object, field: str) -> str:
    if (type(value) is not str or not 1 <= len(value) <= 128
            or _NODE_ID.fullmatch(value) is None):
        raise MeshQuorumError(f"invalid {field}")
    return value


def _bounded_integer(value: object, field: str, *, minimum: int = 0,
                     maximum: int = MAX_TIMESTAMP_NS) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise MeshQuorumError(f"invalid {field}")
    return value


def evaluate_quorum(*, organism_id: str, members: tuple[str, ...],
                    current_epoch: int, vote_receipts: tuple[dict, ...],
                    now_ns: int, seen_vote_ids: tuple[str, ...] = ()) -> QuorumDecision:
    """Evaluate already-authenticated votes for exactly three mesh members.

    A strict 2-of-3 majority is required for ``current_epoch + 1``. Receipts
    are caller-supplied data: this function does not authenticate signatures,
    persist replay state, issue votes, elect a leader, or perform failover.
    ``seen_vote_ids`` must come from a separately durable, trusted replay ledger.
    """
    organism = _node_id(organism_id, "organism id")
    if type(members) is not tuple or len(members) != MAX_MEMBERS:
        raise MeshQuorumError("exactly three members required; two-node failover is unsupported")
    normalized_members = tuple(_node_id(member, "member id") for member in members)
    if tuple(sorted(normalized_members)) != normalized_members:
        raise MeshQuorumError("members must be unique and sorted")
    if len(set(normalized_members)) != MAX_MEMBERS:
        raise MeshQuorumError("members must be unique")

    epoch = _bounded_integer(current_epoch, "current epoch", minimum=1,
                             maximum=MAX_EPOCH - 1) + 1
    now = _bounded_integer(now_ns, "now_ns")

    if type(seen_vote_ids) is not tuple or len(seen_vote_ids) > MAX_SEEN_VOTE_IDS:
        raise MeshQuorumError("invalid seen vote id ledger")
    seen: set[str] = set()
    for vote_id in seen_vote_ids:
        if type(vote_id) is not str or _VOTE_ID.fullmatch(vote_id) is None:
            raise MeshQuorumError("invalid seen vote id")
        if vote_id in seen:
            raise MeshQuorumError("duplicate seen vote id")
        seen.add(vote_id)

    if type(vote_receipts) is not tuple or len(vote_receipts) > MAX_VOTES:
        raise MeshQuorumError("vote receipts must be a tuple of at most three entries")

    voters: set[str] = set()
    vote_ids: set[str] = set()
    candidates: list[str] = []
    canonical_votes: list[dict[str, object]] = []
    for vote in vote_receipts:
        if type(vote) is not dict:
            raise MeshQuorumError("vote receipt has an invalid shape")
        # Reject oversized receipts before key iteration or set allocation.
        if len(vote) != len(_VOTE_FIELDS):
            raise MeshQuorumError("vote receipt has an invalid shape")
        if any(type(key) is not str for key in vote):
            raise MeshQuorumError("vote receipt has an invalid shape")
        if frozenset(vote) != _VOTE_FIELDS:
            raise MeshQuorumError("vote receipt has an invalid shape")
        if type(vote["schema"]) is not str or vote["schema"] != SCHEMA:
            raise MeshQuorumError("unsupported vote schema")
        if type(vote["organism_id"]) is not str or vote["organism_id"] != organism:
            raise MeshQuorumError("organism mismatch")
        voter = _node_id(vote["voter_id"], "voter id")
        candidate = _node_id(vote["candidate_id"], "candidate id")
        if voter not in normalized_members or candidate not in normalized_members:
            raise MeshQuorumError("voter and candidate must be members")
        if voter in voters:
            raise MeshQuorumError("duplicate voter")
        voters.add(voter)
        vote_epoch = _bounded_integer(vote["epoch"], "vote epoch", minimum=1,
                                      maximum=MAX_EPOCH)
        if vote_epoch != epoch:
            raise MeshQuorumError("stale or unexpected vote epoch")
        issued = _bounded_integer(vote["issued_at_ns"], "issued_at_ns")
        expires = _bounded_integer(vote["expires_at_ns"], "expires_at_ns")
        if issued > now or expires <= now or expires <= issued:
            raise MeshQuorumError("vote is future-dated, expired or has invalid validity")
        if expires - issued > MAX_VOTE_TTL_NS:
            raise MeshQuorumError("vote validity exceeds five-minute limit")
        vote_id = vote["vote_id"]
        if type(vote_id) is not str or _VOTE_ID.fullmatch(vote_id) is None:
            raise MeshQuorumError("invalid vote id")
        if vote_id in seen or vote_id in vote_ids:
            raise MeshQuorumError("replayed or duplicate vote id")
        vote_ids.add(vote_id)
        candidates.append(candidate)
        canonical_votes.append({
            "schema": SCHEMA,
            "organism_id": organism,
            "voter_id": voter,
            "candidate_id": candidate,
            "epoch": vote_epoch,
            "issued_at_ns": issued,
            "expires_at_ns": expires,
            "vote_id": vote_id,
        })

    counts = Counter(candidates)
    winner = next((candidate for candidate, count in counts.items()
                   if count >= 2), None)
    support = counts[winner] if winner is not None else max(counts.values(), default=0)
    if winner is not None:
        granted, reason = True, "majority"
    elif len(vote_receipts) < 2:
        granted, reason = False, "insufficient_votes"
    else:
        granted, reason = False, "no_majority"

    canonical_votes.sort(key=lambda item: str(item["voter_id"]))
    payload = {
        "schema": "mojum.mesh-quorum-decision/1",
        "organism_id": organism,
        "members": list(normalized_members),
        "current_epoch": epoch - 1,
        "decision_epoch": epoch,
        "now_ns": now,
        "votes": canonical_votes,
        "granted": granted,
        "candidate_id": winner,
        "support": support,
        "reason": reason,
    }
    digest = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")).hexdigest()
    return QuorumDecision(granted, winner, epoch, len(vote_receipts), support,
                          reason, digest)
