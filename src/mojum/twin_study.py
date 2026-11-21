"""CPU-only, in-memory and deterministic twin-study fixture.

Two independent GENESIS ledgers start with identical inherited parameters.
Human-assigned names, observer-supplied scores and optional encounter markers
are research metadata, NOT a cognitive model, private-storage service or proof
of either experience or learning.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping

from .genesis import GenesisError, GenomeLedger, sha256, validate_seed

STUDY_SCHEMA = "mojum.genesis.twin-study/1"
MEASUREMENT_SCHEMA = "mojum.genesis.twin-measurement/1"
MEETING_SCHEMA = "mojum.genesis.twin-meeting/1"
MAX_MEASUREMENTS = 256  # per subject
MAX_MEETINGS = 128


class TwinStudyError(ValueError):
    """An invalid, noncanonical or stale experimental state."""


def _keys(value, names, what):
    if not isinstance(value, Mapping) or set(value) != set(names):
        raise TwinStudyError(f"{what} requires exactly {sorted(names)}")


def _text(value, what, maximum=128):
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise TwinStudyError(f"{what} must be a nonempty string <= {maximum}")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise TwinStudyError(f"{what} must be valid UTF-8") from exc
    return value


def _integer(value, what, high=2**63 - 1):
    if type(value) is not int or not 0 <= value <= high:
        raise TwinStudyError(f"{what} must be an integer in [0, {high}]")
    return value


def _identity_free(seed):
    return {
        "schema": seed["schema"],
        "revision": seed["revision"],
        "inherited": seed["inherited"],
    }


def _measurement(value, agent_id):
    _keys(value, {"schema", "event_id", "agent_id", "tick", "domain", "score_milli", "source"}, "measurement")
    if value["schema"] != MEASUREMENT_SCHEMA or value["agent_id"] != agent_id:
        raise TwinStudyError("wrong measurement schema or subject")
    _text(value["event_id"], "measurement.event_id")
    _integer(value["tick"], "measurement.tick")
    _text(value["domain"], "measurement.domain")
    _integer(value["score_milli"], "measurement.score_milli", 1000)
    _text(value["source"], "measurement.source")
    return copy.deepcopy(dict(value))


def _meeting(value):
    _keys(value, {"schema", "meeting_id", "tick", "activity"}, "meeting")
    if value["schema"] != MEETING_SCHEMA:
        raise TwinStudyError("wrong meeting schema")
    _text(value["meeting_id"], "meeting_id")
    _integer(value["tick"], "meeting.tick")
    _text(value["activity"], "meeting.activity")
    return copy.deepcopy(dict(value))


def _revision(value):
    try:
        return sha256({key: item for key, item in value.items() if key != "revision"})
    except (GenesisError, UnicodeError) as exc:
        raise TwinStudyError("noncanonical experiment state") from exc


def validate_study(study):
    """Verify all synthetic histories and return an isolated deep copy.

    The revision is an unkeyed content checksum, NOT an authentication token.
    """
    _keys(study, {"schema", "inherited_sha256", "participants", "meetings", "revision"}, "study")
    if study["schema"] != STUDY_SCHEMA:
        raise TwinStudyError("unsupported twin-study schema")
    participants = study["participants"]
    meetings = study["meetings"]
    if type(participants) is not list or len(participants) != 2:
        raise TwinStudyError("exactly two independent participants required")
    if type(meetings) is not list or len(meetings) > MAX_MEETINGS:
        raise TwinStudyError("meeting history exceeds quota or is not a list")
    ids, names, fingerprints = set(), set(), set()
    for subject in participants:
        _keys(subject, {"agent_id", "name", "genome", "measurements"}, "participant")
        agent_id = _text(subject["agent_id"], "agent_id")
        name = _text(subject["name"], "human-assigned name")
        if agent_id in ids or name in names:
            raise TwinStudyError("participant IDs and names must be distinct")
        ids.add(agent_id)
        names.add(name)
        try:
            ledger = GenomeLedger.from_snapshot(subject["genome"])
        except (GenesisError, KeyError, TypeError) as exc:
            raise TwinStudyError("invalid participant genome snapshot") from exc
        seed = ledger.snapshot()["seed"]
        if seed["genome_id"] != agent_id:
            raise TwinStudyError("participant genome identity mismatch")
        fingerprints.add(sha256(_identity_free(seed)))
        measurements = subject["measurements"]
        if type(measurements) is not list or len(measurements) > MAX_MEASUREMENTS:
            raise TwinStudyError("measurement quota exceeded or history is not a list")
        seen, last_tick = set(), -1
        for item in measurements:
            checked = _measurement(item, agent_id)
            if checked["event_id"] in seen or checked["tick"] < last_tick:
                raise TwinStudyError("duplicate measurement or nonmonotonic tick")
            seen.add(checked["event_id"])
            last_tick = checked["tick"]
    if len(fingerprints) != 1 or study["inherited_sha256"] not in fingerprints:
        raise TwinStudyError("twins must share exactly the same inherited template")
    seen_meetings, last_meeting_tick = set(), -1
    for item in meetings:
        checked = _meeting(item)
        if checked["meeting_id"] in seen_meetings or checked["tick"] < last_meeting_tick:
            raise TwinStudyError("duplicate meeting or nonmonotonic tick")
        seen_meetings.add(checked["meeting_id"])
        last_meeting_tick = checked["tick"]
    if type(study["revision"]) is not str or study["revision"] != _revision(study):
        raise TwinStudyError("experiment revision checksum mismatch")
    return copy.deepcopy(dict(study))


def create_study(seed, *, left_id, left_name, right_id, right_name):
    """Create isolated subjects. Only the ID is rewritten in cloned seeds."""
    try:
        template = validate_seed(seed)
    except GenesisError as exc:
        raise TwinStudyError("invalid GENESIS template") from exc
    for label, value in (("left_id", left_id), ("right_id", right_id),
                         ("left_name", left_name), ("right_name", right_name)):
        _text(value, label)
    if left_id == right_id or left_name == right_name:
        raise TwinStudyError("two distinct identities and human-assigned names required")
    subjects = []
    for agent_id, name in ((left_id, left_name), (right_id, right_name)):
        child_seed = copy.deepcopy(template)
        child_seed["genome_id"] = agent_id
        subjects.append({"agent_id": agent_id, "name": name,
                         "genome": GenomeLedger(child_seed).snapshot(), "measurements": []})
    value = {"schema": STUDY_SCHEMA,
             "inherited_sha256": sha256(_identity_free(template)),
             "participants": subjects, "meetings": []}
    value["revision"] = _revision(value)
    return validate_study(value)


def _cas(study, expected_revision):
    checked = validate_study(study)
    if type(expected_revision) is not str or checked["revision"] != expected_revision:
        raise TwinStudyError("stale or missing study revision (CAS failed)")
    return checked


def _subject(study, agent_id):
    for subject in study["participants"]:
        if subject["agent_id"] == agent_id:
            return subject
    raise TwinStudyError("unknown twin subject")


def _finish(study):
    study["revision"] = _revision(study)
    return validate_study(study)


def record_measurement(study, measurement, *, expected_revision):
    """Record an observer-provided score, without modifying any brain or genes."""
    value = _cas(study, expected_revision)
    if not isinstance(measurement, Mapping):
        raise TwinStudyError("measurement must be an object")
    subject = _subject(value, measurement.get("agent_id"))
    checked = _measurement(measurement, subject["agent_id"])
    existing = subject["measurements"]
    if len(existing) >= MAX_MEASUREMENTS:
        raise TwinStudyError("measurement quota reached")
    if any(item["event_id"] == checked["event_id"] for item in existing):
        raise TwinStudyError("duplicate measurement ID")
    if existing and checked["tick"] < existing[-1]["tick"]:
        raise TwinStudyError("cannot backdate a subject measurement")
    if value["meetings"] and checked["tick"] < value["meetings"][-1]["tick"]:
        raise TwinStudyError("cannot backdate a measurement before a recorded meeting")
    existing.append(checked)
    return _finish(value)


def record_meeting(study, meeting, *, expected_revision):
    """Record an optional contact; DO NOT copy histories between subjects."""
    value = _cas(study, expected_revision)
    checked = _meeting(meeting)
    if len(value["meetings"]) >= MAX_MEETINGS:
        raise TwinStudyError("meeting quota reached")
    if any(item["meeting_id"] == checked["meeting_id"] for item in value["meetings"]):
        raise TwinStudyError("duplicate meeting ID")
    previous = [item["tick"] for item in value["meetings"][-1:]]
    previous += [subject["measurements"][-1]["tick"]
                 for subject in value["participants"] if subject["measurements"]]
    if previous and checked["tick"] < max(previous):
        raise TwinStudyError("cannot backdate a joint meeting")
    value["meetings"].append(checked)
    return _finish(value)


def record_acquisition(study, agent_id, acquisition, *, expected_revision):
    """Append GENESIS fixture changes to ONE subject's ledger only."""
    value = _cas(study, expected_revision)
    subject = _subject(value, agent_id)
    if not isinstance(acquisition, Mapping):
        raise TwinStudyError("acquisition must be an object")
    lower_bound = max([item["tick"] for item in subject["measurements"][-1:]]
                      + [item["tick"] for item in value["meetings"][-1:]] + [0])
    if type(acquisition.get("valid_from_tick")) is not int or acquisition["valid_from_tick"] < lower_bound:
        raise TwinStudyError("acquisition may not rewrite already observed ticks")
    try:
        ledger = GenomeLedger.from_snapshot(subject["genome"])
        ledger.append(acquisition, ledger.revision)
    except GenesisError as exc:
        raise TwinStudyError("invalid GENESIS acquisition") from exc
    subject["genome"] = ledger.snapshot()
    return _finish(value)


def compare(study, *, tick):
    """Compare inherited/phenotype parameters and external scores at a tick.

    Differences are descriptive measurements, NOT estimates of intelligence,
    causal effects, subjective experience, or autonomous learning.
    """
    value = validate_study(study)
    _integer(tick, "comparison tick")
    profiles = []
    for subject in value["participants"]:
        ledger = GenomeLedger.from_snapshot(subject["genome"])
        latest = {}
        for item in subject["measurements"]:
            if item["tick"] <= tick:
                latest[item["domain"]] = item["score_milli"]
        profiles.append({"agent_id": subject["agent_id"], "name": subject["name"],
                         "genes": ledger.phenotype(tick)["parameters"],
                         "acquired_count": len(ledger.snapshot()["events"]),
                         "scores_milli": latest})
    left, right = profiles
    domains = sorted(set(left["scores_milli"]) | set(right["scores_milli"]))
    return {
        "schema": "mojum.genesis.twin-comparison/1", "at_tick": tick,
        "study_revision": value["revision"],
        "inherited_sha256": value["inherited_sha256"],
        "subjects": profiles,
        "gene_delta_left_minus_right": {
            gene: left["genes"][gene] - right["genes"][gene]
            for gene in sorted(left["genes"])
        },
        "measured_score_delta_left_minus_right": {
            domain: (left["scores_milli"][domain] - right["scores_milli"][domain]
                     if domain in left["scores_milli"] and domain in right["scores_milli"] else None)
            for domain in domains
        },
        "meetings_through_tick": sum(meeting["tick"] <= tick for meeting in value["meetings"]),
        "classification": "observer_scored_synthetic_fixture_not_cognitive_evidence",
    }
