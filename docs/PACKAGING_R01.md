# Packaging contract and offline verification

Mojum has a setuptools distribution description in pyproject.toml. This guide records the reproducible verification path without adding build tooling to runtime dependencies.

## Contract
- Python 3.11+ runtime with no mandatory third-party dependencies.
- A src/ layout with setuptools package discovery.
- Every project.scripts target must resolve to an importable callable.
- Declared JSON fixtures under package data must be included in the wheel.
- Tests, local tools, SQLite journals, credentials, and private data are not runtime assets.

## Offline wheel build
A fully offline build requires the build backend to be installed locally. Pip resolves build-system requirements before creating the wheel, so a fresh environment without cached setuptools cannot build offline. CI provisions setuptools before the test; the wheel build and archive inspection then run without pip, build isolation, an index, or network access.

```sh
python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

Install without consulting an index:

```sh
python -m pip install --no-deps --no-index --target ./wheel-install dist/*.whl
PYTHONPATH=./wheel-install python -m mojum.aquarium --help
```

Generated console-script launchers live in the target's bin/ directory (POSIX) or Scripts/ directory (Windows). Add the relevant directory to PATH to invoke mojum-aquarium directly.

## Contract tests
From the repository root:

```sh
PYTHONPATH=src python -m unittest tests.test_packaging_contract -v
```

The tests import every declared console-script target, check source-layout/package-data declarations, and build a wheel through the already-installed setuptools backend in a temporary directory. They inspect the archive to require runtime modules and the default graph fixture, while rejecting tests, tools, docs, private/journal/secret paths, environment files, keys, and database artifacts. This is an offline wheel-content check, not a claim that installation on every platform has been exercised.

## Limitations
Packaging the current fixture does not add a real neural backend, MuJoCo physics, distributed persistence, encrypted memory, or a claim of consciousness. The runtime remains a CPU-first synthetic simulation.
