#!/usr/bin/env bash
# Run in a fresh Linux network namespace. The provided directory must be new.
set -euo pipefail
umask 077
unset PYTHONPATH PYTHONHOME
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
comparison_repo=
if [[ -n ${2:-} ]]; then
    comparison_repo=$(cd -- "$2" && pwd)
    : "${SOURCE_DATE_EPOCH:?supply a fixed source timestamp to compare wheel builds}"
fi
release_tag=${3:-}
mkdir -- "${1:?supply a new working directory for the smoke test}"
cd -- "$1"

python -I - <<'PY'
from pathlib import Path

devices = Path("/proc/net/dev").read_text().splitlines()[2:]
interfaces = {line.split(":", 1)[0].strip() for line in devices}
if interfaces != {"lo"}:
    raise SystemExit(f"offline smoke requires a loopback-only namespace, found {interfaces}")
PY

build_wheel() {
    python -I -m pip --isolated --disable-pip-version-check wheel --no-index --no-deps \
        --no-build-isolation --no-cache-dir --wheel-dir "$1" "$2"
}

build_wheel wheels "$repo"
if [[ -n $comparison_repo ]]; then
    build_wheel comparison-wheels "$comparison_repo"
    cmp -- wheels/mojum-*.whl comparison-wheels/mojum-*.whl
fi
python -I -m venv runtime
runtime/bin/python -I -m pip --isolated --disable-pip-version-check install \
    --no-index --no-cache-dir wheels/mojum-*.whl

runtime/bin/python -I - "$repo/LICENSE" "$release_tag" <<'PY'
import json
import os
import subprocess
import sys
import sysconfig
from importlib.metadata import distribution
from importlib.resources import files
from pathlib import Path

import mojum


def run(script, *args, env=None):
    command = [Path(sys.executable).with_name(script), *args]
    return json.loads(subprocess.check_output(command, text=True, env=env))


def run_module(module, *args):
    command = [sys.executable, "-I", "-m", module, *args]
    return json.loads(subprocess.check_output(command, text=True, timeout=60))


assert Path(mojum.__file__).resolve().is_relative_to(Path(sysconfig.get_path("purelib")).resolve())
installed = distribution("mojum")
tag = sys.argv[2]
if tag and tag != f"v{installed.version}":
    raise SystemExit(f"release tag {tag!r} does not match installed version {installed.version!r}")
assert installed.metadata["License-Expression"] == "GPL-3.0-or-later"
assert installed.metadata.get_all("License-File") == ["LICENSE"]
licenses = [path for path in installed.files or () if path.parts[-2:] == ("licenses", "LICENSE")]
assert len(licenses) == 1
assert installed.locate_file(licenses[0]).read_bytes() == Path(sys.argv[1]).read_bytes()

first = run("mojum-aquarium", "--db", "resumed.sqlite3", "--ticks", "120")
assert (first["tick"], first["stored_events"], first["neural_mode"]) == (120, 121, "shadow")
resumed = run("mojum-aquarium", "--db", "resumed.sqlite3", "--ticks", "30")
assert (resumed["tick"], resumed["stored_events"]) == (150, 151)
assert first["journal_head"] != resumed["journal_head"]
assert resumed == run("mojum-aquarium", "--db", "continuous.sqlite3", "--ticks", "150")
native_ordered = sum((1e16, 1.0, -1e16)) == 0.0
other = "compensated" if native_ordered else "ordered"
motor = ("--motor-mode", "developmental_fixture")
run("mojum-aquarium", "--db", "other.sqlite3", "--ticks", "120", "--pulse-sum", other, *motor)
continued = run("mojum-aquarium", "--db", "other.sqlite3", "--ticks", "30", *motor)
assert continued == run("mojum-aquarium", "--db", "other-control.sqlite3", "--ticks", "150", "--pulse-sum", other, *motor)
assert continued["motor_state"]["observations"] == 150
contact = (*motor, "--sensory-mode", "toy_probe")
measured = run("mojum-aquarium", "--db", "contact.sqlite3", "--ticks", "1", *contact)
predicted = run("mojum-aquarium", "--db", "contact.sqlite3", "--ticks", "1", *contact)
assert (predicted["tick"], predicted["stored_events"]) == (2, 3)
assert measured["toy_contact_state"] == predicted["toy_contact_state"]
assert predicted["toy_contact_state"]["table"]["220:70:30"] == {
    "count": 1, "mean_deflection": 0.75,
}
guided = (*contact, "--temporal-mode", "range_guided")
trained = run("mojum-aquarium", "--db", "guided.sqlite3", "--ticks", "5", *guided)
guided_resume = run("mojum-aquarium", "--db", "guided.sqlite3", "--ticks", "1", *guided)
assert (guided_resume["tick"], guided_resume["stored_events"]) == (6, 7)
assert guided_resume["temporal_range_state"]["updates"] == 6
assert trained["motor_state"]["samples"]["north"]["trials"] == 1
assert guided_resume["motor_state"]["samples"]["north"]["trials"] == 2
trace_meta = json.loads(subprocess.check_output([
    sys.executable, "-I", "-m", "mojum.aquarium_trace_export",
    "--db", "guided.sqlite3", "--output", "guided.private.jsonl",
    "--allow-private-export", "--expected-head", guided_resume["journal_head"],
    "--start-tick", "5", "--frame-limit", "1", *guided,
], text=True))
assert trace_meta["frames"] == 1 and trace_meta["journal_head"] == guided_resume["journal_head"]
frame, footer = map(json.loads, Path("guided.private.jsonl").read_text(encoding="utf-8").splitlines())
assert frame["developmental_controller"]["intent"]["kind"] == "north"
assert footer["trace_integrity"]["frame_count"] == 1
seed = json.loads(files("mojum").joinpath("data", "genesis_seed.json").read_text(encoding="utf-8"))
seed["genome_id"] = "wheel-smoke-custom-genesis"
Path("epoch-genesis.json").write_text(json.dumps(seed), encoding="utf-8")
epoch = ("--motor-mode", "local_cell_v4", "--sensory-mode", "toy_probe_v2",
         "--genome", "epoch-genesis.json")
epoch_state = run("mojum-host", "--db", "epoch.sqlite3", "--ticks", "8",
                  "--interval-ms", "20", "--temporal-mode", "none", *epoch)
cold_copy = (sys.executable, "-I", "-m", "mojum.aquarium_cold_copy")
backup = json.loads(subprocess.check_output([
    *cold_copy, "export", "--db", "epoch.sqlite3", "--output", "backup.sqlite3", *epoch,
], text=True))
assert (backup["tick"], backup["event_count"], backup["journal_head"]) == (
    8, 9, epoch_state["journal_head"],
)
assert backup["agent_id"] == seed["genome_id"]
verified = json.loads(subprocess.check_output([
    *cold_copy, "verify", "--db", "backup.sqlite3", "--expected-head", epoch_state["journal_head"],
    "--expected-sha256", backup["db_sha256"], "--expected-tick", "8", *epoch,
], text=True))
assert (verified["agent_id"], verified["journal_head"]) == (
    seed["genome_id"], backup["journal_head"],
)
resumed_backup = run("mojum-aquarium", "--db", "backup.sqlite3", "--ticks", "1",
                     "--expected-head", verified["journal_head"], *epoch)
assert resumed_backup == run("mojum-aquarium", "--db", "epoch.sqlite3", "--ticks", "1", *epoch)
disabled = run("mojum-aquarium", "--db", "disabled.sqlite3", "--ticks", "2", "--neural-mode", "disabled")
assert (disabled["neural_mode"], disabled["neural_state"], disabled["stored_events"]) == ("disabled", None, 3)

fixture = run("mojum-fixture", "--ticks", "2")
assert fixture["tick"] == 2 and fixture["last_episode"]["brain_observation"]["world_light"] == "dim"
graph = json.loads(files("mojum").joinpath("data", "first_loop.json").read_text(encoding="utf-8"))
room = next(node for node in graph["nodes"] if node["kind"] == "world.room")
room_id = room["id"]
unicode_room_id = "room-\u661f"
room["id"] = unicode_room_id
room["config"]["light"] = "bright"
for edge in graph["edges"]:
    for endpoint in edge.values():
        if endpoint["node"] == room_id:
            endpoint["node"] = unicode_room_id
Path("bright.json").write_text(json.dumps(graph), encoding="utf-8")
custom = run("mojum-fixture", "bright.json", "--ticks", "2")
assert custom["last_episode"]["brain_observation"]["world_light"] == "bright"
assert custom["graph_sha256"] != fixture["graph_sha256"]
assert custom["last_frame"][unicode_room_id]["world"]["light"] == "bright"
assert custom == run(
    "mojum-fixture", "bright.json", "--ticks", "2",
    env={**os.environ, "PYTHONIOENCODING": "ascii:strict"},
)

# Experimental labs are distributed in the same wheel. They must read the
# packaged defaults without a source checkout, including on cold restart.
for module, basename, events_key in (
    ("mojum.infant_play", "infant", "events"),
    ("mojum.sensory_curiosity_lab", "curiosity", "private_events"),
):
    journal = f"{basename}.sqlite3"
    started = run_module(module, "--db", journal, "--ticks", "2")
    assert (started["checkpoint"]["tick"], started[events_key]) == (2, 3)
    resumed_lab = run_module(module, "--db", journal, "--ticks", "1")
    assert (resumed_lab["checkpoint"]["tick"], resumed_lab[events_key]) == (3, 4)
    assert resumed_lab == run_module(
        module, "--db", f"{basename}-continuous.sqlite3", "--ticks", "3",
    )

twins = run_module(
    "mojum.twin_lab", "pair", "--left-db", "twin-left.sqlite3",
    "--right-db", "twin-right.sqlite3", "--left-id", "wheel-twin-left",
    "--right-id", "wheel-twin-right",
)
assert [(t["agent_id"], t["tick"], t["stored_events"]) for t in twins["twins"]] == [
    ("wheel-twin-left", 0, 1), ("wheel-twin-right", 0, 1),
]
mentor_options = ("mentor", "--db", "twin-left.sqlite3",
                  "--agent-id", "wheel-twin-left")
mentored = run_module("mojum.twin_lab", *mentor_options, "--ticks", "2")
continued_twin = run_module("mojum.twin_lab", *mentor_options, "--ticks", "1")
assert (mentored["twin"]["tick"], mentored["twin"]["stored_events"]) == (2, 3)
assert (continued_twin["twin"]["tick"], continued_twin["twin"]["stored_events"]) == (3, 4)
assert continued_twin["shared_heritable_sha256"] == twins["shared_heritable_sha256"]
assert run_module("mojum.twin_lab", *mentor_options, "--ticks", "0") == continued_twin
untouched_twin = run_module(
    "mojum.twin_lab", "mentor", "--db", "twin-right.sqlite3",
    "--agent-id", "wheel-twin-right", "--ticks", "0",
)
assert (untouched_twin["twin"]["tick"], untouched_twin["twin"]["stored_events"]) == (0, 1)

print(f'Offline wheel verified: {resumed["tick"]} ticks, {resumed["stored_events"]} events, '
      f'journal head {resumed["journal_head"]}')
PY
