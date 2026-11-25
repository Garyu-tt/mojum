# Mojum local Python install R0.1 — synthetic aquarium only

This package is an **experimental, CPU-first, single-device synthetic fixture**. It is not an autonomous digital organism, genuine neural policy, MuJoCo physical model, mobile build, secure distributed mesh, or hardware-actuation system.

## Requirements

- CPython 3.11 or newer with standard-library `sqlite3`.
- `pip` to install a local wheel. Source builds require `setuptools>=77.0.3` on the builder.
- No mandatory third-party **runtime** dependencies and no GPU.
- Distribution uses the GNU General Public License version 3 or any later version. [Full license text](../LICENSE)

## Install from a local source checkout

With the build tools available locally, install directly from the repository root:

```sh
python -m pip install --no-index --no-build-isolation --no-deps .
```

This fails *explicitly* if the required build tooling is not installed; do not silently invoke an online package index. Normal online development installation may use `python -m pip install -e .`.

Alternatively, build a wheel once for transfer to another compatible machine.

```sh
python -m pip wheel --no-index --no-build-isolation --no-deps --wheel-dir dist .
```

Copy `dist/mojum-0.1.2-py3-none-any.whl` to the target. In a virtual environment there, run the following command from the directory containing the wheel.

```sh
python -m pip install --no-index ./mojum-0.1.2-py3-none-any.whl
```

The source checkout and the installed package contain the same example synthetic GENESIS seed and Universal Graph IR. The installed defaults are read through `importlib.resources`, not through a repository-relative `__file__` path.

## Run with the installed package

Run these commands from any working directory where your Python interpreter can write the SQLite file:

```sh
mojum-aquarium --db ./dante-local.sqlite3 --ticks 120 --neural-mode shadow
mojum-aquarium --db ./dante-local.sqlite3 --ticks 30 --neural-mode shadow
```

The second invocation resumes the same deterministic PRIVATE journal and yields `tick=150`, `stored_events=151`. The output status still explicitly says the brain is not complete.

`mojum-fixture --ticks 10` runs the bundled graph as a short-lived demonstration. Pass a graph path as the first argument to run your own. The persistent aquarium also accepts explicit `--genome` and `--graph` paths.

The selected genome's `genome_id` is the journal's organism identity. To start a separate fixture with a custom genome, use a separate database path and supply the same genome file on each restart.

To let the 2D fixture choose movements using its prior outcomes, use a separate journal and specify the same motor mode each time you reopen it.

```sh
mojum-aquarium --db ./motor-play.sqlite3 --motor-mode developmental_fixture --ticks 120
```

### Moving a shadow journal between Python versions

The v1 pulse predictor stores exact floating-point predictions in the journal. CPython 3.11 and 3.12 can round their sum differently. On open, the Aquarium first checks the full journal using the local summation rule. If a frame differs, it tries the other historical rule from the original genome and world state. A single rule must reproduce every committed frame before the Aquarium can append. Existing event bytes and anchors stay valid.

A very short history can match both rules. In that case `auto` continues with the local rule. To preserve the source's future predictions when moving such a history, pass `--pulse-sum ordered` for a standard CPython 3.11 source or `--pulse-sum compensated` for a standard CPython 3.12/3.13 source. Use the same explicit choice from birth when comparing independent runs across interpreters. [Python 3.12 summation change](https://docs.python.org/3.12/library/functions.html#sum)

## Isolated test

The full stdlib suite remains:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

`tests/test_installable_package.py` checks the source fixtures and command defaults. The package workflow builds the wheel from two clean exports of the same commit using its timestamp for `SOURCE_DATE_EPOCH`, then compares the wheel bytes. It installs the wheel in a fresh virtual environment and runs the console commands in a loopback-only Linux network namespace. It compares the complete checkpoint from 120 ticks plus a 30-tick restart with one from 150 uninterrupted ticks, including the journal hash and learner state.

The workflow also runs for release tags and checks that the installed version matches the tag. The successful Python 3.13 tag job attaches the verified wheel and `SHA256SUMS` to its workflow run.

## Design a custom room from the installed wheel

Create the standalone editor, open the HTML file in your browser, and save
your room as `my-room.json`:

```sh
python -m mojum.room_designer --output ./room-designer.html
```

The editor includes its scripts and works offline. The command prints a byte
count and SHA-256 and refuses to replace an existing file. Validate the saved
draft, then start a new synthetic Aquarium journal in that room:

```sh
mojum-room-check ./my-room.json --format json
mojum-aquarium --db ./custom-room.sqlite3 --room-file ./my-room.json --ticks 120
mojum-aquarium --db ./custom-room.sqlite3 --room-file ./my-room.json --ticks 30
```

Keep the same room file and mode flags when reopening the journal. Changing
its pinned geometry is rejected. The editor is separate from the running
Aquarium and does not modify a journal. See the
[room designer guide](R015_VISUAL_ROOM_DESIGNER.md) for controls and limits.

## Limits

No Android or Pixel 9 on-device execution has been demonstrated. Termux or similar Python distributions might work if they provide compatible CPython, SQLite and packaging tools, but device verification is outstanding. The process does not guarantee indefinite execution while a phone is asleep. Using an ordinary local path does not encrypt the PRIVATE Noosphere journal; guard the path with OS-level access controls and avoid including it in public artifacts. The experimental package is not a production release.
