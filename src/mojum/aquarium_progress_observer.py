"""R0.23 bounded, auditable *synthetic* Aquarium progress metrics.

All observations are derived by streaming an already owner-HMAC verified,
SQLite-immutable R0.22 quarantined history. These are fixture activity
counters, NOT cognition/mental-state inference, grades, consciousness,
true motor capability, wellbeing or learned skill quality.

No raw PRIVATE event, toy identity, numeric coordinate, RGB value,
genetic payload or learned association contents are returned.
"""
from __future__ import annotations

from .quarantined_observer import (
    QuarantinedObserver, ObserverError,
)

SCHEMA = "mojum.aquarium.synthetic-progress-summary/1"
MAX_BINS = 32


def _boolean(value, name):
    if type(value) is not bool:
        raise ObserverError("invalid synthetic progress " + name)
    return value


def _nat(value, name):
    if type(value) is not int or value < 0:
        raise ObserverError("invalid synthetic progress " + name)
    return value


def summarize_synthetic_progress(view, *, include_private_metrics=False,
                                 bins=16):
    """Return a fixed-size aggregate only after explicit PRIVATE opt-in.

    The source must be an R0.22 immutable, owner-HMAC anchored viewer.
    Process at most 262144 already committed source entries with bounded
    memory and fixed-size buckets. Recheck the original anchor and
    quarantine custody before reading any PRIVATE synthetic payload.
    """
    if type(view) is not QuarantinedObserver:
        raise ObserverError("synthetic metrics require immutable signed observer")
    if type(include_private_metrics) is not bool:
        raise ObserverError("include_private_metrics must be bool")
    if not include_private_metrics:
        default = view.summary(include_private_metadata=False)
        default["private_metrics"] = "redacted-needs-explicit-opt-in"
        return default
    if type(bins) is not int or not 1 <= bins <= MAX_BINS:
        raise ObserverError("bins must be integer in 1..32")
    view._validate_custody()
    summary = view.summary(include_private_metadata=False)
    expected_frames = view._db.execute(
        "SELECT COUNT(*) FROM segmented_events WHERE topic='aquarium.frame'"
    ).fetchone()[0]

    histogram = [
        {"bin": i, "frames": 0, "blocked_frames": 0,
         "requested_probes": 0, "measured_contacts": 0,
         "association_updates": 0, "epoch_markers": 0}
        for i in range(bins)
    ]
    bootstrap = 0
    frames = 0
    blocked = 0
    probes = 0
    contacts = 0
    association_updates = 0
    epochs = 0
    other = 0
    for row in view.iter_events(include_nonpublic=True, batch_size=128):
        event = row["event"]
        topic = event["topic"]
        payload = event["payload"]
        if topic == "aquarium.bootstrap":
            bootstrap += 1
            if row["seq"] != 1 or bootstrap != 1:
                raise ObserverError("invalid Aquarium bootstrap position")
        elif topic == "aquarium.frame":
            if bootstrap != 1:
                raise ObserverError("Aquarium frame before bootstrap")
            tick = _nat(payload.get("tick"), "frame tick")
            if tick != frames:
                raise ObserverError("noncontiguous synthetic Aquarium frames")
            if payload.get("physical_model") != "NONE_FIXTURE_ONLY":
                raise ObserverError("unexpected physical Aquarium semantics")
            world = payload.get("world")
            if type(world) is not dict:
                raise ObserverError("missing synthetic world")
            is_blocked = _boolean(world.get("blocked"), "blocked")
            index = min(bins - 1, tick * bins // max(1, expected_frames))
            bucket = histogram[index]
            bucket["frames"] += 1
            blocked += int(is_blocked)
            bucket["blocked_frames"] += int(is_blocked)
            if "toy_contact" in payload:
                toy = payload["toy_contact"]
                if type(toy) is not dict:
                    raise ObserverError("invalid synthetic contact evidence")
                probe = toy.get("probe_intent")
                if type(probe) is not dict:
                    raise ObserverError("invalid synthetic probe intent")
                requested = _boolean(probe.get("requested"), "probe request")
                probes += int(requested)
                bucket["requested_probes"] += int(requested)
                measurement = toy.get("measured_contact")
                if measurement is not None:
                    if not requested or type(measurement) is not dict:
                        raise ObserverError("unsolicited synthetic measurement")
                    actual = _boolean(measurement.get("contact"), "measured contact")
                    contacts += int(actual)
                    bucket["measured_contacts"] += int(actual)
                learned = toy.get("learning")
                if learned is not None:
                    if not requested or type(learned) is not dict:
                        raise ObserverError("unsolicited synthetic learning")
                    update = _boolean(learned.get("learned"), "learned update")
                    association_updates += int(update)
                    bucket["association_updates"] += int(update)
            frames += 1
        elif topic == "aquarium.epoch":
            if bootstrap != 1:
                raise ObserverError("Aquarium epoch before bootstrap")
            number = _nat(payload.get("epoch_index"), "epoch")
            if number != epochs + 1:
                raise ObserverError("noncontiguous synthetic v4 epochs")
            absolute_tick = _nat(payload.get("absolute_tick"), "epoch tick")
            if absolute_tick != frames:
                raise ObserverError("epoch marker displaced from synthetic frame count")
            index = min(bins - 1, frames * bins // max(1, expected_frames))
            histogram[index]["epoch_markers"] += 1
            epochs += 1
        else:
            other += 1
    if bootstrap != 1 or frames != expected_frames:
        raise ObserverError("not an identifiable canonical Aquarium synthetic history")
    if (bootstrap + frames + epochs + other != summary["journal_events"]):
        raise ObserverError("source rows changed during historical aggregation")
    # Recheck externally signed head to reject concurrently swapped files.
    final = view.verify_chain(
        external_expected_head=view._receipt["source_head"]
    )
    if final["count"] != summary["journal_events"]:
        raise ObserverError("historical source changed during aggregation")
    return {
        "schema": SCHEMA,
        "agent_id": view.agent_id,
        "source_head": final["head"],
        "journal_events": final["count"],
        "motor_frames": frames,
        "bootstrap_events": bootstrap,
        "epoch_markers": epochs,
        "blocked_motor_frames": blocked,
        "requested_synthetic_probes": probes,
        "measured_synthetic_contacts": contacts,
        "synthetic_association_updates": association_updates,
        "other_source_events": other,
        "histogram": histogram,
        "custody": "QUARANTINED_READ_ONLY",
        "source_trust": "owner-hmac-signed-selected-history",
        "privacy": "explicit-synthetic-private-aggregates-only",
        "interpretation": "recorded-fixture-activity-not-grades-or-cognition",
    }
