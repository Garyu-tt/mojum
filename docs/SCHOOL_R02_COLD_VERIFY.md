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

This is a **local integrity/reproducibility check**, not an authentic digital signature, consent verification, independent evaluation of creative skill, or crash-durable replica protocol. On POSIX platforms with `O_DIRECTORY` and `O_NOFOLLOW`, the verifier pins the original directory descriptor for enumeration and all reads, opens files relative to it without following final-component symlinks, and rejects nonregular entries after opening. Renaming the directory or swapping an entry to a symlink during verification cannot redirect the reads. Non-POSIX hosts retain best-effort path checks and make **no filesystem race-resistance claim**. This does not authenticate a malicious same-UID writer or prevent concurrent in-place modification of already opened regular files. Use a trusted catalog and a directory owned by the operator. The verifier does not change source files or create journal entries.

Targeted tests: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_school_fixture_verify.py' -v`. The repository CI matrix is the source of multi-version execution results.
