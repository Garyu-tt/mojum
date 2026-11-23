# Host Doctor R0.1

## Purpose

The read-only installation diagnostic checks the two public bootstrap resources bundled with the installed Mojum package:

- genesis_seed.json, using the canonical GENESIS validate_seed contract.
- first_loop.json, using the canonical Universal Graph v1 validator.

## Run

    PYTHONPATH=src python -m mojum.host_doctor

The command emits one bounded JSON report with stable check identifiers and reason codes. Exit status is 0 only when both packaged fixtures pass, otherwise 1.

## Safety and scope

The command does not construct an Aquarium, open or create SQLite, inspect a user-supplied path, read PRIVATE Noosphere records, contact the network, import optional native engines, or dump environment and host information. It verifies package resources only. Output omits resource paths, fixture contents, genome identifiers and graph payloads.

Duplicate JSON keys, invalid UTF-8, non-finite values, over-budget fixtures and canonical contract violations fail closed. The diagnostic limits genome input to 16 KiB and graph input to 256 KiB; the graph validator independently enforces its canonical limits.

This is an installation preflight, not a simulation health check, hardware capability report, security attestation, or proof that SQLite is writable. It never changes or repairs an installation.

## Tests

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_host_doctor.py' -v
