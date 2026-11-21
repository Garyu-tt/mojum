# R0.2 school: cold PRIVATE fixture verification

The CPU-only art/music runner writes a **synthetic scripted fixture**, not evidence of autonomous learning, grades, or an attached Noosphere database. Before reusing a saved fixture, the operator can verify its exact bytes against a trusted copy of the originating catalog.

```sh
PYTHONPATH=src python -m mojum.school \
  --catalog examples/school_catalog_r01.json --section visual_arts \
  --start-tick 40 --out ./private-art-fixture

PYTHONPATH=src python -m mojum.school_verify \
  --catalog examples/school_catalog_r01.json \
  --fixture ./private-art-fixture
```

Repeat with `--section music` and a fresh `--out` directory. For a no-change baseline, add `--mode frozen_control` to the creation command. The cold verifier checks the exact canonical manifest, expected event sequence and PRIVATE labels, allowed filenames, every saved PPM/JSON artifact and its deterministic SHA-256 provenance by regenerating from the provided catalog and virtual start tick. It rejects missing, extra, oversized, symlinked, modified or malformed files. The output is only a small PRIVATE summary without event payloads or art/music content.

This is a **local integrity/reproducibility check**, not an authentic digital signature, filesystem race hardening, consent verification, independent evaluation of creative skill, or crash-durable replica protocol. Use a trusted catalog and a directory owned by the operator. The verifier does not change source files or create journal entries.

Targeted tests: `PYTHONPATH=src python -m unittest -v tests.test_school_fixture_verify`. The repository CI matrix is the source of multi-version execution results.
