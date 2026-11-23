"""Offline replication lag measurement; advisory only, never a failover authority."""
from dataclasses import dataclass
import re

SCHEMA = "mojum.mesh-replication-lag/1"
_MAX_I64 = (1 << 63) - 1
_STREAM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)


@dataclass(frozen=True)
class CheckpointHead:
    """Caller-supplied public checkpoint metadata, not proof of durable storage."""
    stream_id: str
    epoch: int
    sequence: int
    committed_at_ns: int
    state_sha256: str


@dataclass(frozen=True)
class ReplicationLagReport:
    schema: str
    stream_id: str
    epoch: int
    source_sequence: int
    replica_sequence: int
    sequence_lag: int
    replica_age_ns: int
    source_age_ns: int
    max_sequence_lag: int
    max_replica_age_ns: int
    status: str


def _bounded_int(value: object, *, minimum: int, maximum: int, name: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid {name}")
    return value


def _validate_head(head: object, name: str) -> CheckpointHead:
    if type(head) is not CheckpointHead:
        raise ValueError(f"{name} must be an exact CheckpointHead")
    if (type(head.stream_id) is not str
            or _STREAM_ID.fullmatch(head.stream_id) is None):
        raise ValueError(f"invalid {name} stream id")
    _bounded_int(head.epoch, minimum=0, maximum=(1 << 31) - 1,
                 name=f"{name} epoch")
    _bounded_int(head.sequence, minimum=0, maximum=_MAX_I64,
                 name=f"{name} sequence")
    _bounded_int(head.committed_at_ns, minimum=0, maximum=_MAX_I64,
                 name=f"{name} commit time")
    if type(head.state_sha256) is not str or _DIGEST.fullmatch(head.state_sha256) is None:
        raise ValueError(f"invalid {name} state digest")
    return head


def measure_replication_lag(
    source: CheckpointHead,
    replica: CheckpointHead,
    *,
    now_ns: int,
    max_sequence_lag: int = 0,
    max_replica_age_ns: int = 5_000_000_000,
) -> ReplicationLagReport:
    """Compare two explicit heads under caller-chosen thresholds.

    "current" means only that both measured lag values are within the supplied
    thresholds. This does not verify peer identity, ancestry, durable copies,
    signatures, or authority to read/write/promote a replica.
    """
    source = _validate_head(source, "source")
    replica = _validate_head(replica, "replica")
    _bounded_int(now_ns, minimum=0, maximum=_MAX_I64, name="now_ns")
    _bounded_int(max_sequence_lag, minimum=0, maximum=_MAX_I64,
                 name="max_sequence_lag")
    _bounded_int(max_replica_age_ns, minimum=0, maximum=_MAX_I64,
                 name="max_replica_age_ns")

    if source.stream_id != replica.stream_id:
        raise ValueError("checkpoint stream mismatch")
    if source.epoch != replica.epoch:
        raise ValueError("checkpoint epoch mismatch")
    if replica.sequence > source.sequence:
        raise ValueError("replica is ahead of source")
    if replica.committed_at_ns > source.committed_at_ns:
        raise ValueError("replica timestamp is ahead of source")
    if source.committed_at_ns > now_ns or replica.committed_at_ns > now_ns:
        raise ValueError("checkpoint timestamp is in the future")
    if replica.sequence == source.sequence and replica.state_sha256 != source.state_sha256:
        raise ValueError("equal sequence has divergent state digest")

    sequence_lag = source.sequence - replica.sequence
    replica_age = now_ns - replica.committed_at_ns
    source_age = now_ns - source.committed_at_ns
    if replica_age > max_replica_age_ns:
        status = "stale"
    elif sequence_lag > max_sequence_lag:
        status = "lagging"
    else:
        status = "current"

    return ReplicationLagReport(
        schema=SCHEMA,
        stream_id=source.stream_id,
        epoch=source.epoch,
        source_sequence=source.sequence,
        replica_sequence=replica.sequence,
        sequence_lag=sequence_lag,
        replica_age_ns=replica_age,
        source_age_ns=source_age,
        max_sequence_lag=max_sequence_lag,
        max_replica_age_ns=max_replica_age_ns,
        status=status,
    )
