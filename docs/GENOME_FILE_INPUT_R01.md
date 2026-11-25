# Aquarium genome-file input

`mojum.aquarium` and both `mojum.aquarium_cold_copy` subcommands load
`--genome` through `mojum.genome_file.load_seed_file()` before opening the
journal or writing a backup. Their packaged default seed uses the same
admission path. The genotype schema and persisted journal format are unchanged.

## Accepted input

- A regular local file, including a symlink that resolves to a regular file.
- At most **262,144 bytes (256 KiB)**, including whitespace and UTF-8 encoding.
- Strict UTF-8 JSON with exactly one value, no duplicate object keys at any
  depth, and no `NaN`, `Infinity`, or `-Infinity` constants.
- A seed accepted by the existing `mojum.genesis.validate_seed()` contract.

Key comparisons happen after JSON string decoding, so `genome_id` and
`genome_\u0069d` are the same key. Repeated keys are rejected even when their
values match. Input must have an unambiguous organism identity and inherited
parameters before a journal can be created or resumed.

## File handling and errors

The reader validates the actual opened descriptor with `fstat()` before
reading. On POSIX it uses `O_NONBLOCK` so a FIFO with no writer is rejected
without waiting for another process. Pipes, sockets, devices and directories
are not genome files. Caller-owned numeric file descriptors are not accepted.
The reader closes its own descriptor on success and rejection.

The byte limit is enforced by reading at most the limit plus one byte before
UTF-8 decoding or JSON parsing. It does not depend on a pathname size check,
which could become stale. Parse-depth failures, invalid encodings and I/O
failures become bounded input diagnostics; duplicate-key errors do not echo
untrusted keys. A symlink is allowed, but its opened target must still be a
regular file.

Invalid input exits the CLI with status **2** and no traceback. Admission
precedes Aquarium construction and cold-copy export/verification, so rejected
input does not create a journal/backup or change an existing one. The existing
Aquarium diagnostic for missing or malformed input remains
`--genome requires a readable UTF-8 JSON file`; schema errors retain their
`invalid --genome` prefix and canonical validation detail.

## Verification

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_genome_file*.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_aquarium_cli_preflight.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p '*cold_copy*.py' -v
```

Regressions use disposable synthetic seeds and SQLite journals. They cover
byte boundaries, duplicate keys, descriptor ownership/cleanup, special-file
rejection, unchanged journals on invalid input, valid restart and a verified
cold-copy roundtrip. POSIX-specific checks are skipped where unsupported.

This is a local input boundary, not file authentication or an immutable
filesystem snapshot. It cannot impose a deadline on an unhealthy filesystem.
Other CLI genome readers and direct Python callers keep their existing
contracts; this change applies to the two CLI modules named above.
