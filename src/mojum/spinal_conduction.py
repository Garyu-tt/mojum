"""Bounded deterministic delay queues for synthetic spinal experiments.

The queue operates on caller-declared logical route identifiers and integer
ticks.  It does not read sensors, schedule wall-clock work, model biological
conduction, learn delays, or authorize physical actuation.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


PROFILE_SCHEMA = "mojum.spinal-conduction.profile/1"
SIGNAL_SCHEMA = "mojum.spinal-conduction.signal/1"
SNAPSHOT_SCHEMA = "mojum.spinal-conduction.snapshot/1"
DELIVERY_SCHEMA = "mojum.spinal-conduction.delivery/1"
FRAME_SCHEMA = "mojum.spinal-conduction.frame/1"
PUBLIC_SYNTHETIC = "PUBLIC_SYNTHETIC"

MAX_ROUTES = 64
MAX_ROUTE_DELAY_TICKS = 4_096
MAX_PENDING_SIGNALS = 1_024
MAX_SIGNALS_PER_TICK = 128
MAX_ID_CHARS = 96
MAX_REVISION = 2**63 - 1
MAX_TICK = 2**63 - 1
MAX_SIGNAL_MILLI = 1_000
MAX_PROFILE_BYTES = 65_536
MAX_SNAPSHOT_BYTES = 1_048_576
MAX_DELIVERY_BYTES = 8_192

_IDENTIFIER_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
)
_IDENTIFIER_START = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)
_PROFILE_KEYS = frozenset(
    {"schema", "visibility", "consent", "revision", "max_pending", "routes"}
)
_ROUTE_KEYS = frozenset({"route_id", "delay_ticks"})
_SIGNAL_KEYS = frozenset(
    {
        "schema",
        "visibility",
        "consent",
        "event_id",
        "source_id",
        "source_revision",
        "route_id",
        "signal_milli",
    }
)


class SpinalConductionError(ValueError):
    """An untrusted profile, signal batch, or snapshot is invalid."""


@dataclass(frozen=True, slots=True)
class ConductionRoute:
    """One caller-declared logical route and its integer-tick delay."""

    route_id: str
    delay_ticks: int


@dataclass(frozen=True, slots=True)
class ConductionProfile:
    """Immutable canonical queue limits and route delays."""

    schema: str
    visibility: str
    consent: str
    revision: int
    max_pending: int
    routes: tuple[ConductionRoute, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class PendingSignal:
    """One admitted signal waiting for its due logical tick."""

    event_id: str
    source_id: str
    source_revision: int
    route_id: str
    emitted_tick: int
    due_tick: int
    delay_ticks: int
    signal_milli: int


@dataclass(frozen=True, slots=True)
class ConductionSnapshot:
    """Immutable queue state; validation occurs at the restore boundary."""

    schema: str
    profile_revision: int
    profile_sha256: str
    tick: int
    pending: tuple[PendingSignal, ...]
    state_sha256: str


@dataclass(frozen=True, slots=True)
class ConductionDelivery:
    """Deterministic receipt for one signal delivered at a logical tick."""

    schema: str
    visibility: str
    consent: str
    profile_revision: int
    profile_sha256: str
    event_id: str
    source_id: str
    source_revision: int
    route_id: str
    emitted_tick: int
    delivered_tick: int
    delay_ticks: int
    signal_milli: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ConductionFrame:
    """One tick result with no actuation authority."""

    schema: str
    tick: int
    profile_revision: int
    profile_sha256: str
    deliveries: tuple[ConductionDelivery, ...]
    pending_count: int
    state_sha256: str
    physical_actuation: bool


def _fail(message: str) -> None:
    raise SpinalConductionError(message)


def _native_int(value: object, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        _fail(f"{label} must be a native integer in [{low}, {high}]")
    return value


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= MAX_ID_CHARS:
        _fail(f"{label} must be 1..{MAX_ID_CHARS} characters")
    if value[0] not in _IDENTIFIER_START or any(
        character not in _IDENTIFIER_CHARS for character in value
    ):
        _fail(f"{label} must be restricted ASCII and start with an alphanumeric")
    return value


def _exact_dict(value: object, keys: frozenset[str], label: str) -> dict:
    if type(value) is not dict:
        _fail(f"{label} must be a native dict")
    if len(value) != len(keys):
        _fail(f"{label} must contain exactly {sorted(keys)}")
    if any(type(key) is not str for key in value):
        _fail(f"{label} contains a non-string key")
    if set(value) != keys:
        _fail(f"{label} must contain exactly {sorted(keys)}")
    return value


def _canonical_bytes(value: object, maximum: int, label: str) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise SpinalConductionError(f"{label} is not canonical JSON") from exc
    if len(encoded) > maximum:
        _fail(f"canonical {label} exceeds {maximum} bytes")
    return encoded


def _sha256(value: object, maximum: int, label: str) -> str:
    return hashlib.sha256(_canonical_bytes(value, maximum, label)).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _profile_payload(profile: ConductionProfile) -> dict[str, object]:
    return {
        "schema": profile.schema,
        "visibility": profile.visibility,
        "consent": profile.consent,
        "revision": profile.revision,
        "max_pending": profile.max_pending,
        "routes": [
            {"route_id": route.route_id, "delay_ticks": route.delay_ticks}
            for route in profile.routes
        ],
    }


def _validate_profile(profile: object) -> ConductionProfile:
    if type(profile) is not ConductionProfile:
        _fail("profile must be an exact compiled ConductionProfile")
    if (
        profile.schema != PROFILE_SCHEMA
        or profile.visibility != PUBLIC_SYNTHETIC
        or profile.consent != "explicit"
    ):
        _fail("profile envelope is unsupported")
    _native_int(profile.revision, 0, MAX_REVISION, "profile revision")
    _native_int(profile.max_pending, 1, MAX_PENDING_SIGNALS, "profile max_pending")
    if type(profile.routes) is not tuple or not 1 <= len(profile.routes) <= MAX_ROUTES:
        _fail(f"profile routes must be an exact tuple containing 1..{MAX_ROUTES} routes")
    prior = None
    for route in profile.routes:
        if type(route) is not ConductionRoute:
            _fail("profile contains a noncanonical route")
        route_id = _identifier(route.route_id, "profile route_id")
        _native_int(
            route.delay_ticks,
            0,
            MAX_ROUTE_DELAY_TICKS,
            "profile route delay",
        )
        if prior is not None and route_id <= prior:
            _fail("profile routes must be unique and canonically sorted")
        prior = route_id
    if not _valid_sha256(profile.sha256):
        _fail("profile digest must be lowercase SHA-256")
    expected = _sha256(_profile_payload(profile), MAX_PROFILE_BYTES, "profile")
    if profile.sha256 != expected:
        _fail("profile digest mismatch")
    return profile


def compile_conduction_profile(specification: object) -> ConductionProfile:
    """Validate and detach one bounded synthetic conduction profile."""

    value = _exact_dict(specification, _PROFILE_KEYS, "profile")
    if value["schema"] != PROFILE_SCHEMA:
        _fail("profile schema is unsupported")
    if value["visibility"] != PUBLIC_SYNTHETIC or value["consent"] != "explicit":
        _fail("profile must be explicitly PUBLIC_SYNTHETIC with explicit consent")
    revision = _native_int(value["revision"], 0, MAX_REVISION, "profile revision")
    max_pending = _native_int(
        value["max_pending"], 1, MAX_PENDING_SIGNALS, "profile max_pending"
    )
    supplied_routes = value["routes"]
    if type(supplied_routes) is not list or not 1 <= len(supplied_routes) <= MAX_ROUTES:
        _fail(f"profile routes must be a native list containing 1..{MAX_ROUTES} routes")
    routes: list[ConductionRoute] = []
    seen: set[str] = set()
    for index, supplied in enumerate(supplied_routes):
        route = _exact_dict(supplied, _ROUTE_KEYS, f"profile.routes[{index}]")
        route_id = _identifier(route["route_id"], f"profile.routes[{index}].route_id")
        if route_id in seen:
            _fail("profile contains a duplicate route_id")
        seen.add(route_id)
        routes.append(
            ConductionRoute(
                route_id=route_id,
                delay_ticks=_native_int(
                    route["delay_ticks"],
                    0,
                    MAX_ROUTE_DELAY_TICKS,
                    f"profile.routes[{index}].delay_ticks",
                ),
            )
        )
    routes.sort(key=lambda route: route.route_id)
    provisional = ConductionProfile(
        schema=PROFILE_SCHEMA,
        visibility=PUBLIC_SYNTHETIC,
        consent="explicit",
        revision=revision,
        max_pending=max_pending,
        routes=tuple(routes),
        sha256="0" * 64,
    )
    compiled = ConductionProfile(
        schema=provisional.schema,
        visibility=provisional.visibility,
        consent=provisional.consent,
        revision=provisional.revision,
        max_pending=provisional.max_pending,
        routes=provisional.routes,
        sha256=_sha256(_profile_payload(provisional), MAX_PROFILE_BYTES, "profile"),
    )
    return _validate_profile(compiled)


def _pending_payload(signal: PendingSignal) -> dict[str, object]:
    return {
        "event_id": signal.event_id,
        "source_id": signal.source_id,
        "source_revision": signal.source_revision,
        "route_id": signal.route_id,
        "emitted_tick": signal.emitted_tick,
        "due_tick": signal.due_tick,
        "delay_ticks": signal.delay_ticks,
        "signal_milli": signal.signal_milli,
    }


def _pending_key(signal: PendingSignal) -> tuple[int, str, str, str, int]:
    return (
        signal.due_tick,
        signal.route_id,
        signal.event_id,
        signal.source_id,
        signal.source_revision,
    )


def _snapshot_payload(
    profile: ConductionProfile,
    tick: int,
    pending: tuple[PendingSignal, ...],
) -> dict[str, object]:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "profile_revision": profile.revision,
        "profile_sha256": profile.sha256,
        "tick": tick,
        "pending": [_pending_payload(signal) for signal in pending],
    }


def _build_snapshot(
    profile: ConductionProfile,
    tick: int,
    pending: tuple[PendingSignal, ...],
) -> ConductionSnapshot:
    payload = _snapshot_payload(profile, tick, pending)
    return ConductionSnapshot(
        schema=SNAPSHOT_SCHEMA,
        profile_revision=profile.revision,
        profile_sha256=profile.sha256,
        tick=tick,
        pending=pending,
        state_sha256=_sha256(payload, MAX_SNAPSHOT_BYTES, "conduction snapshot"),
    )


def _delivery(profile: ConductionProfile, tick: int, signal: PendingSignal) -> ConductionDelivery:
    payload = {
        "schema": DELIVERY_SCHEMA,
        "visibility": PUBLIC_SYNTHETIC,
        "consent": "explicit",
        "profile_revision": profile.revision,
        "profile_sha256": profile.sha256,
        "event_id": signal.event_id,
        "source_id": signal.source_id,
        "source_revision": signal.source_revision,
        "route_id": signal.route_id,
        "emitted_tick": signal.emitted_tick,
        "delivered_tick": tick,
        "delay_ticks": signal.delay_ticks,
        "signal_milli": signal.signal_milli,
    }
    return ConductionDelivery(
        **payload,
        sha256=_sha256(payload, MAX_DELIVERY_BYTES, "delivery receipt"),
    )


class SpinalConductionQueue:
    """Deterministic bounded logical-tick delay queue."""

    __slots__ = ("_profile", "_tick", "_pending")

    def __init__(self, profile: ConductionProfile):
        self._profile = _validate_profile(profile)
        self._tick = 0
        self._pending: tuple[PendingSignal, ...] = ()

    @property
    def profile(self) -> ConductionProfile:
        return self._profile

    @property
    def tick(self) -> int:
        return self._tick

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def snapshot(self) -> ConductionSnapshot:
        return _build_snapshot(self._profile, self._tick, self._pending)

    @classmethod
    def from_snapshot(
        cls,
        profile: ConductionProfile,
        snapshot: ConductionSnapshot,
    ) -> "SpinalConductionQueue":
        profile = _validate_profile(profile)
        if type(snapshot) is not ConductionSnapshot:
            _fail("snapshot must be an exact ConductionSnapshot")
        if snapshot.schema != SNAPSHOT_SCHEMA:
            _fail("snapshot schema is unsupported")
        _native_int(snapshot.profile_revision, 0, MAX_REVISION, "snapshot profile revision")
        _native_int(snapshot.tick, 0, MAX_TICK, "snapshot tick")
        if (
            snapshot.profile_revision != profile.revision
            or snapshot.profile_sha256 != profile.sha256
        ):
            _fail("snapshot is bound to a different profile")
        if type(snapshot.pending) is not tuple or len(snapshot.pending) > profile.max_pending:
            _fail("snapshot pending queue exceeds its profile bound")
        route_delays = {route.route_id: route.delay_ticks for route in profile.routes}
        seen: set[str] = set()
        validated: list[PendingSignal] = []
        for signal in snapshot.pending:
            if type(signal) is not PendingSignal:
                _fail("snapshot contains a noncanonical pending signal")
            event_id = _identifier(signal.event_id, "pending event_id")
            source_id = _identifier(signal.source_id, "pending source_id")
            source_revision = _native_int(
                signal.source_revision, 0, MAX_REVISION, "pending source revision"
            )
            route_id = _identifier(signal.route_id, "pending route_id")
            if route_id not in route_delays:
                _fail("snapshot references an unknown route")
            emitted_tick = _native_int(
                signal.emitted_tick, 1, snapshot.tick, "pending emitted tick"
            )
            delay_ticks = _native_int(
                signal.delay_ticks, 0, MAX_ROUTE_DELAY_TICKS, "pending delay"
            )
            due_tick = _native_int(signal.due_tick, 1, MAX_TICK, "pending due tick")
            signal_milli = _native_int(
                signal.signal_milli,
                -MAX_SIGNAL_MILLI,
                MAX_SIGNAL_MILLI,
                "pending signal",
            )
            if delay_ticks != route_delays[route_id] or due_tick != emitted_tick + delay_ticks:
                _fail("snapshot pending delay does not match its profile")
            if due_tick <= snapshot.tick:
                _fail("snapshot retains a signal that was already due")
            if event_id in seen:
                _fail("snapshot contains a duplicate pending event_id")
            seen.add(event_id)
            validated.append(
                PendingSignal(
                    event_id=event_id,
                    source_id=source_id,
                    source_revision=source_revision,
                    route_id=route_id,
                    emitted_tick=emitted_tick,
                    due_tick=due_tick,
                    delay_ticks=delay_ticks,
                    signal_milli=signal_milli,
                )
            )
        canonical = tuple(sorted(validated, key=_pending_key))
        if snapshot.pending != canonical:
            _fail("snapshot pending queue is not canonically ordered")
        if not _valid_sha256(snapshot.profile_sha256) or not _valid_sha256(
            snapshot.state_sha256
        ):
            _fail("snapshot digest must be lowercase SHA-256")
        expected = _build_snapshot(profile, snapshot.tick, canonical)
        if snapshot.state_sha256 != expected.state_sha256:
            _fail("snapshot state digest mismatch")
        restored = cls(profile)
        restored._tick = snapshot.tick
        restored._pending = canonical
        return restored

    def step(self, tick: int, signals: object) -> ConductionFrame:
        """Admit one contiguous tick, enqueue its signals, and deliver due work.

        All admission and capacity checks complete before queue state changes.
        Signal input order is intentionally insignificant; deliveries use a
        stable canonical ordering.
        """

        if self._tick >= MAX_TICK:
            _fail("conduction queue lifetime tick budget exhausted")
        next_tick = _native_int(tick, 1, MAX_TICK, "step tick")
        if next_tick != self._tick + 1:
            _fail("step tick must be the next contiguous logical tick")
        if type(signals) is not list or len(signals) > MAX_SIGNALS_PER_TICK:
            _fail(
                f"signals must be a native list containing at most {MAX_SIGNALS_PER_TICK} records"
            )
        route_delays = {
            route.route_id: route.delay_ticks for route in self._profile.routes
        }
        seen = {signal.event_id for signal in self._pending}
        admitted: list[PendingSignal] = []
        for index, supplied in enumerate(signals):
            value = _exact_dict(supplied, _SIGNAL_KEYS, f"signals[{index}]")
            if value["schema"] != SIGNAL_SCHEMA:
                _fail(f"signals[{index}] has an unsupported schema")
            if value["visibility"] != PUBLIC_SYNTHETIC or value["consent"] != "explicit":
                _fail(f"signals[{index}] must be explicitly PUBLIC_SYNTHETIC with consent")
            event_id = _identifier(value["event_id"], f"signals[{index}].event_id")
            if event_id in seen:
                _fail("signal event_id is already pending or duplicated in the batch")
            seen.add(event_id)
            source_id = _identifier(value["source_id"], f"signals[{index}].source_id")
            source_revision = _native_int(
                value["source_revision"],
                0,
                MAX_REVISION,
                f"signals[{index}].source_revision",
            )
            route_id = _identifier(value["route_id"], f"signals[{index}].route_id")
            if route_id not in route_delays:
                _fail(f"signals[{index}] references an unknown route")
            delay_ticks = route_delays[route_id]
            if next_tick > MAX_TICK - delay_ticks:
                _fail("signal due tick exceeds the queue lifetime bound")
            admitted.append(
                PendingSignal(
                    event_id=event_id,
                    source_id=source_id,
                    source_revision=source_revision,
                    route_id=route_id,
                    emitted_tick=next_tick,
                    due_tick=next_tick + delay_ticks,
                    delay_ticks=delay_ticks,
                    signal_milli=_native_int(
                        value["signal_milli"],
                        -MAX_SIGNAL_MILLI,
                        MAX_SIGNAL_MILLI,
                        f"signals[{index}].signal_milli",
                    ),
                )
            )
        proposed = tuple(sorted(self._pending + tuple(admitted), key=_pending_key))
        due = tuple(signal for signal in proposed if signal.due_tick <= next_tick)
        retained = tuple(signal for signal in proposed if signal.due_tick > next_tick)
        if len(retained) > self._profile.max_pending:
            _fail("conduction queue pending capacity exceeded")
        deliveries = tuple(_delivery(self._profile, next_tick, signal) for signal in due)
        candidate = _build_snapshot(self._profile, next_tick, retained)

        self._tick = next_tick
        self._pending = retained
        return ConductionFrame(
            schema=FRAME_SCHEMA,
            tick=next_tick,
            profile_revision=self._profile.revision,
            profile_sha256=self._profile.sha256,
            deliveries=deliveries,
            pending_count=len(retained),
            state_sha256=candidate.state_sha256,
            physical_actuation=False,
        )
