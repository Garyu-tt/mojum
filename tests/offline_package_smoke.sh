#!/usr/bin/env bash
# Run in a fresh Linux network namespace. The provided directory must be new.
set -euo pipefail
umask 077
unset PYTHONPATH PYTHONHOME
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -- "${1:?supply a new working directory for the smoke test}"
cd -- "$1"

python -I - <<'PY'
from pathlib import Path

devices = Path("/proc/net/dev").read_text().splitlines()[2:]
interfaces = {line.split(":", 1)[0].strip() for line in devices}
if interfaces != {"lo"}:
    raise SystemExit(f"offline smoke requires a loopback-only namespace, found {interfaces}")
PY

python -I -m pip --isolated --disable-pip-version-check wheel --no-index --no-deps \
    --no-build-isolation --no-cache-dir --wheel-dir wheels "$repo"
python -I -m venv runtime
runtime/bin/python -I -m pip --isolated --disable-pip-version-check install \
    --no-index --no-cache-dir wheels/mojum-*.whl

runtime/bin/python -I - <<'PY'
import json
import subprocess
import sys
import sysconfig
from importlib.resources import files
from pathlib import Path

import mojum


def run(script, *args):
    command = [Path(sys.executable).with_name(script), *args]
    return json.loads(subprocess.check_output(command, text=True))


assert Path(mojum.__file__).resolve().is_relative_to(Path(sysconfig.get_path("purelib")).resolve())
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
disabled = run("mojum-aquarium", "--db", "disabled.sqlite3", "--ticks", "2", "--neural-mode", "disabled")
assert (disabled["neural_mode"], disabled["neural_state"], disabled["stored_events"]) == ("disabled", None, 3)

fixture = run("mojum-fixture", "--ticks", "2")
assert fixture["tick"] == 2 and fixture["last_episode"]["brain_observation"]["world_light"] == "dim"
graph = json.loads(files("mojum").joinpath("data", "first_loop.json").read_text(encoding="utf-8"))
room = next(node for node in graph["nodes"] if node["kind"] == "world.room")
room["config"]["light"] = "bright"
Path("bright.json").write_text(json.dumps(graph), encoding="utf-8")
custom = run("mojum-fixture", "bright.json", "--ticks", "2")
assert custom["last_episode"]["brain_observation"]["world_light"] == "bright"
assert custom["graph_sha256"] != fixture["graph_sha256"]

print(f'Offline wheel verified: {resumed["tick"]} ticks, {resumed["stored_events"]} events, '
      f'journal head {resumed["journal_head"]}')
PY
