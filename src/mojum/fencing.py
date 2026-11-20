"""Pure CPU-only epoch fencing contract for simulated mesh writers.

This is a local test fixture, not a distributed consensus implementation.
"""
from dataclasses import dataclass


class FenceError(ValueError):
    """Reject ambiguous or stale writer authority."""


@dataclass(frozen=True)
class Lease:
    epoch: int
    writer: str
    revision: int

    def __post_init__(self):
        if type(self.epoch) is not int or self.epoch < 1:
            raise FenceError("epoch must be a positive integer")
        if type(self.revision) is not int or self.revision < 0:
            raise FenceError("revision must be a nonnegative integer")
        if not isinstance(self.writer, str) or not self.writer.strip():
            raise FenceError("writer must be nonempty")


def authorize(current: Lease, proposal: Lease) -> Lease:
    """Authorize one append against the caller-supplied authoritative state.

    No election or quorum is implied. A new epoch must start at revision zero;
    an existing epoch advances by exactly one revision with the same writer.
    Callers MUST persist/compare-and-swap the returned lease atomically.
    """
    if not isinstance(current, Lease) or not isinstance(proposal, Lease):
        raise FenceError("leases required")
    if proposal.epoch == current.epoch:
        if proposal.writer != current.writer or proposal.revision != current.revision + 1:
            raise FenceError("conflicting writer or revision")
    elif proposal.epoch == current.epoch + 1:
        if proposal.revision != 0:
            raise FenceError("new epoch must begin at revision zero")
    else:
        raise FenceError("stale or skipped epoch")
    return proposal
