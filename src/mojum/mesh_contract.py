"""Offline mesh capability negotiation; NOT discovery, authentication or enrollment."""
from dataclasses import dataclass

PROTOCOL = "mojum.mesh-capabilities/1"


@dataclass(frozen=True)
class Offer:
    protocol: str
    node_id: str
    capabilities: tuple[str, ...]


def _validated(offer: Offer) -> frozenset[str]:
    if not isinstance(offer, Offer) or offer.protocol != PROTOCOL:
        raise ValueError("unsupported mesh protocol")
    if not isinstance(offer.node_id, str) or not (1 <= len(offer.node_id) <= 128):
        raise ValueError("invalid node id")
    if not isinstance(offer.capabilities, tuple) or len(offer.capabilities) > 128:
        raise ValueError("invalid capabilities")
    for name in offer.capabilities:
        if not isinstance(name, str) or not (1 <= len(name) <= 128):
            raise ValueError("invalid capability name")
        if not all(ch.isascii() and (ch.islower() or ch.isdigit() or ch in "._-/") for ch in name):
            raise ValueError("invalid capability name")
    if len(set(offer.capabilities)) != len(offer.capabilities):
        raise ValueError("duplicate capability")
    return frozenset(offer.capabilities)


def negotiate(local: Offer, remote: Offer, *, authenticated: bool = False,
              enrolled: bool = False) -> tuple[str, ...]:
    """Intersect offers only after caller-supplied independent trust gates.

    Authentication and enrollment MUST be verified by a separate trusted layer.
    Boolean flags are not proof of identity or authorization.
    """
    ours = _validated(local)
    theirs = _validated(remote)
    if local.node_id == remote.node_id:
        raise ValueError("same node identity")
    if type(authenticated) is not bool or type(enrolled) is not bool:
        raise ValueError("trust flags must be boolean")
    if not (authenticated and enrolled):
        raise PermissionError("untrusted mesh peer")
    return tuple(sorted(ours & theirs))
