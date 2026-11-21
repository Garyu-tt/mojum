# Mojum local Python install R0.1 — synthetic aquarium only

This package is an **experimental, CPU-first, single-device synthetic fixture**. It is not an autonomous digital organism, genuine neural policy, MuJoCo physical model, mobile build, secure distributed mesh, or hardware-actuation system.

## Requirements

- CPython 3.11 or newer with standard-library `sqlite3`.
- `pip` to install a local wheel. Only source builds use `setuptools>=61` and older setuptools versions also need `wheel` available on the builder.
- No mandatory third-party **runtime** dependencies and no GPU.
- **License undecided:** this in-repository install support does not grant permission to redistribute or publish on PyPI.

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

Copy `dist/mojum-0.1.0-py3-none-any.whl` to the target. In a virtual environment there, run the following command from the directory containing the wheel.

```sh
python -m pip install --no-index ./mojum-0.1.0-py3-none-any.whl
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

## Isolated test

The full stdlib suite remains:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

`tests/test_installable_package.py` checks the source fixtures and command defaults. The package workflow provisions the build tools, then builds the wheel, installs it in a fresh virtual environment, and runs both console commands outside the checkout in a Linux network namespace with only loopback. It compares the complete checkpoint from 120 ticks plus a 30-tick restart with one from 150 uninterrupted ticks, including the journal hash and learner state.

## Limits

No Android or Pixel 9 on-device execution has been demonstrated. Termux or similar Python distributions might work if they provide compatible CPython, SQLite and packaging tools, but device verification is outstanding. The process does not guarantee indefinite execution while a phone is asleep. Using an ordinary local path does not encrypt the PRIVATE Noosphere journal; guard the path with OS-level access controls and avoid including it in public artifacts. The experimental package is not a production release.
