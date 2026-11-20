# Mojum local Python install R0.1 — synthetic aquarium only

This package is an **experimental, CPU-first, single-device synthetic fixture**. It is not an autonomous digital organism, genuine neural policy, MuJoCo physical model, mobile build, secure distributed mesh, or hardware-actuation system.

## Requirements

- CPython 3.11 or newer with standard-library `sqlite3`.
- A local Python packaging backend (`setuptools>=61`) and `pip` for installation.
- No mandatory third-party **runtime** dependencies and no GPU.
- **License undecided:** this in-repository install support does not grant permission to redistribute or publish on PyPI.

## Install from a local source checkout

For an already-provisioned machine with `setuptools` available locally, a no-network installation command is:

```sh
python -m pip install --no-index --no-build-isolation --no-deps .
```

This fails *explicitly* if the required build tooling is not installed; do not silently invoke an online package index. Normal online development installation may use `python -m pip install -e .`.

The source checkout and the installed package contain the same example synthetic GENESIS seed and Universal Graph IR. The installed defaults are read through `importlib.resources`, not through a repository-relative `__file__` path.

## Run with the installed package

Run these commands from any working directory where your Python interpreter can write the SQLite file:

```sh
mojum-aquarium --db ./dante-local.sqlite3 --ticks 120 --neural-mode shadow
mojum-aquarium --db ./dante-local.sqlite3 --ticks 30 --neural-mode shadow
```

The second invocation resumes the same deterministic PRIVATE journal and yields `tick=150`, `stored_events=151`. The output status still explicitly says the brain is not complete.

`mojum-fixture <graph.json> --ticks 10` is a separate, short-lived graph demonstration, not the persistent aquarium. Existing commands `python -m mojum.aquarium ...` and explicit `--genome` / `--graph` overrides are preserved.

## Isolated test

The full stdlib suite remains:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

`tests/test_installable_package.py` additionally launches the aquarium from a temporary directory outside the checkout and verifies deterministic SQLite restart. The dedicated package smoke workflow installs the project and launches the console command **outside** the source directory.

## Limits

No Android or Pixel 9 on-device execution has been demonstrated. Termux or similar Python distributions might work if they provide compatible CPython, SQLite and packaging tools, but device verification is outstanding. The process does not guarantee indefinite execution while a phone is asleep. Using an ordinary local path does not encrypt the PRIVATE Noosphere journal; guard the path with OS-level access controls and avoid including it in public artifacts. The experimental package is not a production release.
