"""CPU-only, opt-in twin fixture: independent PRIVATE journals, no live brain."""
from __future__ import annotations

import argparse
import copy
import json
from importlib.resources import files
import math
import os
from pathlib import Path

from .aquarium import Aquarium, AquariumError, KINDS, MAX_REPLAY_EVENTS, _movement
from .genesis import sha256, validate_seed
from .graph import validate as validate_graph

RECEIPT_SCHEMA = "mojum.twin-lab.synthetic-receipt/1"


def _seed_for(template, agent_id):
    clone = copy.deepcopy(template)
    clone["genome_id"] = agent_id
    return validate_seed(clone)


def _fingerprint(template):
    # IDs belong to individual actors, not their shared inherited traits.
    return sha256({key: template[key] for key in ("schema", "revision", "inherited")})


def _check_graph(graph):
    validate_graph(graph)
    if sorted(node["kind"] for node in graph["nodes"]) != sorted(KINDS):
        raise AquariumError("twin lab requires the canonical six-node fixture graph")


def _new_path(path):
    path = Path(path)
    if not path.name or str(path) == ":memory:":
        raise AquariumError("explicit persistent SQLite path required")
    if path.is_symlink() or path.exists():
        raise AquariumError("refusing an existing or symlinked journal")
    if not path.parent.is_dir():
        raise AquariumError("journal parent directory does not exist")
    if any((Path(str(path) + s).exists() or Path(str(path) + s).is_symlink())
           for s in ("-wal", "-shm")):
        raise AquariumError("journal has pre-existing SQLite sidecar")
    return path


def _reserve_new_path(path):
    """Reserve a fresh journal name atomically; never open an existing path."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise AquariumError("refusing an existing or symlinked journal") from exc
    except OSError as exc:
        raise AquariumError("cannot exclusively reserve a new twin journal") from exc
    try:
        stat = os.fstat(fd)
        return stat.st_dev, stat.st_ino
    finally:
        os.close(fd)


def _undo_reservation(path, identity):
    """Undo only our empty reservation if the second exclusive create failed."""
    try:
        stat = path.lstat()
        if (stat.st_dev, stat.st_ino) == identity and path.is_file():
            path.unlink()
    except FileNotFoundError:
        pass


def _summary(a):
    return {"agent_id": a.agent_id, "tick": a.tick,
            "position_m": [a.world.x, a.world.y],
            "stored_events": a.stored_events}


def create_pair(left_db, right_db, left_id, right_id, template, graph):
    """Initialize independent zero-step organisms, never an existing journal.

    Creation is only atomic *per SQLite database*, not across both files.
    """
    template = validate_seed(template)
    left_seed = _seed_for(template, left_id)
    right_seed = _seed_for(template, right_id)
    if left_id == right_id:
        raise AquariumError("twins need different human-selected IDs")
    if left_id == template["genome_id"] or right_id == template["genome_id"]:
        raise AquariumError("new twins must not reuse the source organism ID")
    _check_graph(graph)
    left_db, right_db = _new_path(left_db), _new_path(right_db)
    if left_db.resolve(strict=False) == right_db.resolve(strict=False):
        raise AquariumError("twins cannot share the same SQLite file")
    # SQLite normally opens existing files. Reserve BOTH names with O_EXCL
    # first so a competing creator cannot insert a journal after preflight.
    # This is not a cross-file transaction or protection from a hostile
    # process with the same account's write permissions.
    reserved = []
    try:
        for path in (left_db, right_db):
            reserved.append((path, _reserve_new_path(path)))
    except BaseException:
        for path, identity in reversed(reserved):
            _undo_reservation(path, identity)
        raise
    with Aquarium(left_db, left_seed, graph, agent_id=left_id) as left:
        with Aquarium(right_db, right_seed, graph, agent_id=right_id) as right:
            return {
                "schema": RECEIPT_SCHEMA,
                "experiment": "separate_synthetic_twins_not_a_developmental_brain",
                "shared_heritable_sha256": _fingerprint(template),
                "twins": [_summary(left), _summary(right)],
                "journals": "two_independent_PRIVATE_SQLite_files_not_secure_replicas",
            }


def mentor_steps(db, agent_id, template, graph, *, ticks, dx, dy):
    """Advance exactly one existing twin with externally chosen movements."""
    template = validate_seed(template)
    if agent_id == template["genome_id"]:
        raise AquariumError("twin mentor must not target the source organism")
    seed = _seed_for(template, agent_id)
    _check_graph(graph)
    if type(ticks) is not int or not 0 <= ticks <= 1000:
        raise AquariumError("ticks must be integer within 0..1000")
    dx, dy = _movement(dx), _movement(dy)
    if math.hypot(dx, dy) > 1000.0:
        raise AquariumError("combined displacement exceeds fixture budget")
    db = Path(db)
    if db.is_symlink() or not db.is_file():
        raise AquariumError("mentor requires an existing regular SQLite journal")
    # A regular file is not necessarily an existing organism. Validate the
    # complete journal before a writable open can initialize storage, change
    # its journal mode or permissions. Pin that revision across the reopen.
    with Aquarium(db, seed, graph, agent_id=agent_id, read_only=True) as twin:
        if twin.stored_events + ticks > MAX_REPLAY_EVENTS:
            raise AquariumError("requested ticks exceed remaining event budget")
        expected_head = twin.checkpoint()["journal_head"]
        summary = _summary(twin)
    # A zero-step request is inspection only; it needs no writable connection.
    if ticks:
        with Aquarium(db, seed, graph, agent_id=agent_id,
                      external_expected_head=expected_head) as twin:
            if twin.stored_events + ticks > MAX_REPLAY_EVENTS:
                raise AquariumError("requested ticks exceed remaining event budget")
            for _ in range(ticks):
                twin.step(dx=dx, dy=dy)
            summary = _summary(twin)
    return {
        "schema": RECEIPT_SCHEMA,
        "experiment": "scripted_mentor_not_learned_policy",
        "shared_heritable_sha256": _fingerprint(template),
        "twin": summary,
        "journal": "PRIVATE_local_SQLite_not_exported",
    }


def main():
    parser = argparse.ArgumentParser(description="Mojum offline synthetic twin lab")
    packaged = files("mojum").joinpath("data")
    commands = parser.add_subparsers(dest="command", required=True)
    pair = commands.add_parser("pair", help="create two new private local journals")
    for flag in ("left", "right"):
        pair.add_argument("--" + flag + "-db", type=Path, required=True)
        pair.add_argument("--" + flag + "-id", required=True)
    mentor = commands.add_parser("mentor", help="append scripted steps to one twin")
    mentor.add_argument("--db", type=Path, required=True)
    mentor.add_argument("--agent-id", required=True)
    mentor.add_argument("--ticks", type=int, default=10)
    mentor.add_argument("--dx", type=float, default=0.02)
    mentor.add_argument("--dy", type=float, default=0.0)
    for command in (pair, mentor):
        command.add_argument("--genome", type=Path,
                             default=packaged.joinpath("genesis_seed.json"))
        command.add_argument("--graph", type=Path,
                             default=packaged.joinpath("first_loop.json"))
    args = parser.parse_args()
    template = json.loads(args.genome.read_text(encoding="utf-8"))
    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    try:
        if args.command == "pair":
            result = create_pair(args.left_db, args.right_db, args.left_id,
                                 args.right_id, template, graph)
        else:
            result = mentor_steps(args.db, args.agent_id, template, graph,
                                  ticks=args.ticks, dx=args.dx, dy=args.dy)
    except (AquariumError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
