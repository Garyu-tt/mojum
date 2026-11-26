# Noosphere bitemporal fact-conflict audit R0.1

The schema is mojum.noosphere.validity-audit/1. is a pure standard-library diagnostic over caller-supplied synthetic facts. It complements the append-only Noosphere event journal; it is not a journal reader, memory backend, merge policy, access-control system, or conflict resolver.

## Input contract

Each fact has exactly these fields:

| Field | Contract |
|---|---|
| event_id | Unique nonempty native UTF-8 string, at most 256 characters |
| fact_key | Nonempty native UTF-8 identity key, at most 256 characters |
| visibility | Exactly PUBLIC |
| provenance | Exactly PUBLIC_SYNTHETIC, explicitly supplied by the caller |
| valid_from_ns | Native integer in [0, 2^63-1] |
| valid_to_ns | null for an unbounded end, otherwise an integer strictly greater than start |
| value | Bounded native JSON tree |

Intervals use half-open semantics: [valid_from_ns, valid_to_ns). Touching endpoints do not overlap. A null end is treated as positive infinity. Two facts with the same key and identical canonical value digests may overlap without a conflict. Different values under the same key are reported only when their validity intervals overlap.

## Output and deterministic behavior

The report contains only event IDs, a SHA-256 digest of the fact key, value digests, overlap bounds, counts, a deterministic report digest, and explicit PUBLIC_SYNTHETIC_ONLY / NOT_PERFORMED labels. Raw values and the source payload are never copied into findings. Results are sorted independently of input ordering. No attempt is made to select a winner, infer truth, merge facts, or rewrite source data.

## Resource and trust boundaries

- At most 4,096 facts, 250,000 overlap-pair comparisons and 25,000 emitted conflicts.
- At most 16 KiB per canonical value, 4 MiB total canonical values, 100,000 value nodes and depth 32.
- JSON wire envelope at most 8 MiB; duplicate object keys, invalid UTF-8, non-finite values, custom Python objects, container cycles/shared aliases and non-native key types are rejected.
- audit_validity() performs no file, database, network, model, or host access. audit_json() parses only the supplied bytes/string.
- PUBLIC classification and PUBLIC_SYNTHETIC provenance are caller assertions, not cryptographic proof or a declassification mechanism. PRIVATE/RESTRICTED data must not be passed to this API.

## Verification

Run the isolated suite with:

  PYTHONPATH=src python -m unittest discover -s tests -p 'test_noosphere_validity.py' -v

This utility does not establish correctness of the underlying facts or guarantee absence of every possible semantic conflict. It reports only conflicts under the explicit fact_key and valid-time model supplied by the caller.
