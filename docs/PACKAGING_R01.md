# Packaging contract and offline verification

Mojum has a setuptools distribution description in pyproject.toml. This guide records the reproducible verification path without adding build tooling to runtime dependencies.

## Contract
- Python 3.11+ runtime with no mandatory third-party dependencies.
- A src/ layout with setuptools package discovery.
- Every project.scripts target must resolve to an importable callable.
- Declared JSON fixtures under package data must be included in the wheel.
- Tests, local tools, SQLite journals, credentials, and private data are not runtime assets.

## Offline wheel build
A fully offline build requires the build backend to be installed locally. Pip resolves build-system requirements before creating the wheel, so a fresh environment without cached setuptools cannot build offline.

```sh
python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

Install without consulting an index:

```sh
python -m pip install --no-deps --no-index --target ./wheel-install dist/*.whl
PYTHONPATH=./wheel-install python -m mojum.aquarium --help
```

Generated console-script launchers live in the target's bin/ directory (POSIX) or Scripts/ directory (Windows). Add the relevant directory to PATH to invoke mojum-aquarium directly.

## Static contract tests
From the repository root:

```sh
PYTHONPATH=src python -m unittest tests.test_packaging_contract -v
```

The tests import each declared console-script target and verify it is callable, check source-layout/package-data declarations, and confirm the default graph fixture exists. They do not claim every platform's wheel installation has been exercised; run the wheel commands above for that smoke check.

## Limitations
Packaging the current fixture does not add a real neural backend, MuJoCo physics, distributed persistence, encrypted memory, or a claim of consciousness. The runtime remains a CPU-first synthetic simulation.