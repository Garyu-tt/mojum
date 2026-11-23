# GENESIS phenotype audit R0.1

## Purpose

This opt-in, read-only helper verifies a caller-supplied phenotype against
the canonical GENESIS ledger contract at one explicit logical tick. It is
designed to catch stale, cross-tick, edited, or incorrectly assembled
phenotype projections before a downstream consumer uses them.

## API

    from mojum.genesis_phenotype_audit import audit_phenotype
    report = audit_phenotype(
        ledger_snapshot, phenotype_snapshot, tick=120,
        source_classification="PUBLIC_SYNTHETIC",
    )
    if report["status"] != "match":
        raise ValueError("phenotype does not match its ledger and tick")

The deterministic report includes schema, status, tick, ledger revision,
expected/observed phenotype SHA-256 digests, and sorted mismatched top-level
field names. It never returns acquisition payloads or the reconstructed
phenotype.

## Bounds and failure behavior

- Caller must explicitly supply the exact `PUBLIC_SYNTHETIC` classification;
  the gate rejects missing, private, or malformed classifications. This is a
  caller assertion, not independent provenance verification.
- Standard-library only; no network, filesystem, SQLite, native engine, or
  live runtime access.
- Native JSON dictionaries/lists only, maximum depth 32 and 20,000 values.
- Each input is limited to 1 MiB of canonical UTF-8 JSON.
- Tick must be an exact integer in [0, 2^63-1]; booleans are rejected.
- Ledger validation delegates to GenomeLedger.from_snapshot, including its
  schema, event, monotonic-time, effective-range, event-count and revision
  checks.
- Malformed inputs raise GenesisPhenotypeAuditError. A well-formed but
  incorrect projection returns status="mismatch" with field names only.

## Trust and compatibility

No GENESIS wire schema or historical snapshot is changed. Hashes are content
checksums, not signatures, authentication, authorization, or proof of
provenance. The report verifies only the supplied synthetic fixture. It is
not a biological model, trained organism, cognition claim, or durable
persistence/replication mechanism.

Run focused tests from the repository root:

    PYTHONPATH=src python -m unittest discover -s tests -p "test_genesis_phenotype_audit.py" -v
