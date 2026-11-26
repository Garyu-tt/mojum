"""Bounded routing metadata for synthetic surface-contact fixtures.

The map relates caller-declared surface region identifiers to logical spinal
segments or separately namespaced cranial paths.  It does not infer anatomy,
read a sensor, locate a contact in a world, or integrate with a spinal runtime.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


MAP_SCHEMA = "mojum.spinal-sensory-map/1"
CONTACT_SCHEMA = "mojum.spinal-sensory-contact/1"
ROUTE_SCHEMA = "mojum.spinal-sensory-route/1"
PUBLIC_SYNTHETIC = "PUBLIC_SYNTHETIC"

SPINAL_SEGMENTS = tuple(
    f"{region}{index}"
    for region, count in (("C", 8), ("T", 12), ("L", 5), ("S", 5), ("Co", 1))
    for index in range(1, count + 1)
)
MAX_REGIONS = 256
MAX_ROUTES_PER_REGION = 8
MAX_TOTAL_ROUTES = 1024
MAX_CANONICAL_BYTES = 131_072
MAX_RECEIPT_BYTES = 8_192
MAX_ID_CHARS = 96
MAX_REVISION = 2**31 - 1
MAX_COUNTER = 2**63 - 1
MAX_DENSITY_PPM = 1_000_000
MAX_SIGNAL_PPM = 1_000_000
MAX_LOD = 7

_SPINAL_SEGMENT_SET = frozenset(SPINAL_SEGMENTS)
_SPINAL_ORDER = {segment: index for index, segment in enumerate(SPINAL_SEGMENTS)}
_IDENTIFIER_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
)
_IDENTIFIER_START_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)
_CRANIAL_SUFFIX_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
)
_MAP_KEYS = frozenset({
    "schema", "visibility", "consent", "map_id", "revision", "regions",
})
_REGION_KEYS = frozenset({
    "region_id", "pathway", "routes", "density_ppm", "lod",
})
_CONTACT_KEYS = frozenset({
    "schema", "visibility", "consent", "map_id", "map_revision",
    "event_id", "tick", "source_id", "source_revision", "region_id",
    "signal_ppm",
})


class SpinalSensoryMapError(ValueError):
    """Malformed or unsupported synthetic sensory-map input."""


@dataclass(frozen=True, slots=True)
class SensoryRegion:
    """One detached region-to-route association admitted by the contract."""

    region_id: str
    pathway: str
    routes: tuple[str, ...]
    density_ppm: int
    lod: int


@dataclass(frozen=True, slots=True)
class SpinalSensoryMap:
    """Immutable canonical map; no caller containers are retained."""

    map_id: str
    revision: int
    regions: tuple[SensoryRegion, ...]
    spinal_region_count: int
    cranial_region_count: int
    route_assignment_count: int
    shared_spinal_segments: tuple[str, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class RoutingReceipt:
    """Immutable provenance-bearing result of routing one admitted contact."""

    schema: str
    map_id: str
    map_revision: int
    map_sha256: str
    event_id: str
    tick: int
    source_id: str
    source_revision: int
    region_id: str
    pathway: str
    routes: tuple[str, ...]
    density_ppm: int
    lod: int
    signal_ppm: int
    sha256: str


def _fail(message: str) -> None:
    raise SpinalSensoryMapError(message)


def _native_int(
    value: object,
    name: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_COUNTER,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(f"{name} must be a native integer in [{minimum}, {maximum}]")
    return value


def _identifier(value: object, name: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= MAX_ID_CHARS:
        _fail(f"{name} must be nonempty ASCII text of at most {MAX_ID_CHARS} characters")
    if value[0] not in _IDENTIFIER_START_CHARS or any(
        character not in _IDENTIFIER_CHARS for character in value
    ):
        _fail(f"{name} contains an unsupported identifier character")
    return value


def _exact_object(value: object, keys: frozenset[str], name: str) -> dict:
    if type(value) is not dict:
        _fail(f"{name} must be a native dict")
    if any(type(key) is not str for key in value):
        _fail(f"{name} contains a non-string key")
    if set(value) != keys:
        _fail(f"{name} must contain exactly {sorted(keys)}")
    return value


def _require_envelope(value: dict, schema: str, name: str) -> None:
    if type(value["schema"]) is not str or value["schema"] != schema:
        _fail(f"{name} has an unsupported schema")
    if type(value["visibility"]) is not str or value["visibility"] != PUBLIC_SYNTHETIC:
        _fail(f"{name} must be explicitly PUBLIC_SYNTHETIC")
    if type(value["consent"]) is not str or value["consent"] != "explicit":
        _fail(f"{name} requires explicit consent")


def _cranial_route(value: object, name: str) -> str:
    route = _identifier(value, name)
    prefix = "cranial:"
    if not route.startswith(prefix):
        _fail(f"{name} must use the cranial: namespace")
    suffix = route[len(prefix):]
    if not suffix or suffix[0] not in _IDENTIFIER_START_CHARS or any(
        character not in _CRANIAL_SUFFIX_CHARS for character in suffix
    ):
        _fail(f"{name} has an invalid cranial path identifier")
    return route


def _canonical_bytes(value: object, name: str, maximum: int) -> bytes:
    try:
        canonical = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise SpinalSensoryMapError(f"{name} is not canonical JSON") from exc
    if len(canonical) > maximum:
        _fail(f"canonical {name} exceeds {maximum} bytes")
    return canonical


def compile_sensory_map(specification: object) -> SpinalSensoryMap:
    """Validate and canonicalize one bounded synthetic routing map.

    Region and route ordering is intentionally insignificant.  Spinal routes
    are emitted in the 31-segment logical order, while cranial path identifiers
    are emitted lexicographically.  Different regions may share a segment.
    """
    value = _exact_object(specification, _MAP_KEYS, "map")
    _require_envelope(value, MAP_SCHEMA, "map")
    map_id = _identifier(value["map_id"], "map.map_id")
    revision = _native_int(
        value["revision"], "map.revision", maximum=MAX_REVISION
    )
    supplied_regions = value["regions"]
    if type(supplied_regions) is not list or not 1 <= len(supplied_regions) <= MAX_REGIONS:
        _fail(f"map.regions must be a native list containing 1..{MAX_REGIONS} records")

    admitted_regions: list[dict[str, object]] = []
    seen_region_ids: set[str] = set()
    spinal_usage = {segment: 0 for segment in SPINAL_SEGMENTS}
    route_assignment_count = 0

    for index, supplied_region in enumerate(supplied_regions):
        label = f"map.regions[{index}]"
        region = _exact_object(supplied_region, _REGION_KEYS, label)
        region_id = _identifier(region["region_id"], f"{label}.region_id")
        if region_id in seen_region_ids:
            _fail("map contains a duplicate region_id")
        seen_region_ids.add(region_id)

        pathway = region["pathway"]
        if type(pathway) is not str or pathway not in ("spinal", "cranial"):
            _fail(f"{label}.pathway must be spinal or cranial")
        supplied_routes = region["routes"]
        if type(supplied_routes) is not list or not 1 <= len(supplied_routes) <= MAX_ROUTES_PER_REGION:
            _fail(
                f"{label}.routes must be a native list containing "
                f"1..{MAX_ROUTES_PER_REGION} routes"
            )

        routes: list[str] = []
        for route_index, supplied_route in enumerate(supplied_routes):
            route_label = f"{label}.routes[{route_index}]"
            if pathway == "spinal":
                if (
                    type(supplied_route) is not str
                    or supplied_route not in _SPINAL_SEGMENT_SET
                ):
                    _fail(f"{route_label} must be one of the 31 logical spinal segments")
                route = supplied_route
            else:
                route = _cranial_route(supplied_route, route_label)
            if route in routes:
                _fail(f"{label}.routes contains a duplicate route")
            routes.append(route)

        route_assignment_count += len(routes)
        if route_assignment_count > MAX_TOTAL_ROUTES:
            _fail(f"map exceeds the {MAX_TOTAL_ROUTES}-route assignment budget")
        if pathway == "spinal":
            routes.sort(key=_SPINAL_ORDER.__getitem__)
            for route in routes:
                spinal_usage[route] += 1
        else:
            routes.sort()

        admitted_regions.append({
            "region_id": region_id,
            "pathway": pathway,
            "routes": routes,
            "density_ppm": _native_int(
                region["density_ppm"],
                f"{label}.density_ppm",
                minimum=1,
                maximum=MAX_DENSITY_PPM,
            ),
            "lod": _native_int(
                region["lod"], f"{label}.lod", maximum=MAX_LOD
            ),
        })

    admitted_regions.sort(key=lambda region: region["region_id"])
    canonical_map = {
        "schema": MAP_SCHEMA,
        "visibility": PUBLIC_SYNTHETIC,
        "consent": "explicit",
        "map_id": map_id,
        "revision": revision,
        "regions": admitted_regions,
    }
    canonical = _canonical_bytes(canonical_map, "sensory map", MAX_CANONICAL_BYTES)
    regions = tuple(
        SensoryRegion(
            region_id=region["region_id"],
            pathway=region["pathway"],
            routes=tuple(region["routes"]),
            density_ppm=region["density_ppm"],
            lod=region["lod"],
        )
        for region in admitted_regions
    )
    return SpinalSensoryMap(
        map_id=map_id,
        revision=revision,
        regions=regions,
        spinal_region_count=sum(region.pathway == "spinal" for region in regions),
        cranial_region_count=sum(region.pathway == "cranial" for region in regions),
        route_assignment_count=route_assignment_count,
        shared_spinal_segments=tuple(
            segment for segment in SPINAL_SEGMENTS if spinal_usage[segment] > 1
        ),
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


def route_contact(
    sensory_map: SpinalSensoryMap,
    contact: object,
) -> RoutingReceipt:
    """Route one exact-shape contact to its caller-declared logical paths.

    Density and LOD are carried as metadata; this function does not rescale the
    signal or infer additional routes.  The receipt digest is deterministic but
    is not a signature or an authenticity proof.
    """
    if type(sensory_map) is not SpinalSensoryMap:
        _fail("sensory_map must be a compiled SpinalSensoryMap")
    value = _exact_object(contact, _CONTACT_KEYS, "contact")
    _require_envelope(value, CONTACT_SCHEMA, "contact")
    map_id = _identifier(value["map_id"], "contact.map_id")
    map_revision = _native_int(
        value["map_revision"], "contact.map_revision", maximum=MAX_REVISION
    )
    if map_id != sensory_map.map_id or map_revision != sensory_map.revision:
        _fail("contact map identity or revision does not match the compiled map")

    event_id = _identifier(value["event_id"], "contact.event_id")
    tick = _native_int(value["tick"], "contact.tick")
    source_id = _identifier(value["source_id"], "contact.source_id")
    source_revision = _native_int(
        value["source_revision"],
        "contact.source_revision",
        maximum=MAX_REVISION,
    )
    region_id = _identifier(value["region_id"], "contact.region_id")
    signal_ppm = _native_int(
        value["signal_ppm"],
        "contact.signal_ppm",
        maximum=MAX_SIGNAL_PPM,
    )
    region = next(
        (candidate for candidate in sensory_map.regions if candidate.region_id == region_id),
        None,
    )
    if region is None:
        _fail("contact references an unknown region_id")

    snapshot = {
        "schema": ROUTE_SCHEMA,
        "visibility": PUBLIC_SYNTHETIC,
        "consent": "explicit",
        "map_id": sensory_map.map_id,
        "map_revision": sensory_map.revision,
        "map_sha256": sensory_map.sha256,
        "event_id": event_id,
        "tick": tick,
        "source_id": source_id,
        "source_revision": source_revision,
        "region_id": region.region_id,
        "pathway": region.pathway,
        "routes": list(region.routes),
        "density_ppm": region.density_ppm,
        "lod": region.lod,
        "signal_ppm": signal_ppm,
    }
    canonical = _canonical_bytes(snapshot, "routing receipt", MAX_RECEIPT_BYTES)
    return RoutingReceipt(
        schema=ROUTE_SCHEMA,
        map_id=sensory_map.map_id,
        map_revision=sensory_map.revision,
        map_sha256=sensory_map.sha256,
        event_id=event_id,
        tick=tick,
        source_id=source_id,
        source_revision=source_revision,
        region_id=region.region_id,
        pathway=region.pathway,
        routes=region.routes,
        density_ppm=region.density_ppm,
        lod=region.lod,
        signal_ppm=signal_ppm,
        sha256=hashlib.sha256(canonical).hexdigest(),
    )
