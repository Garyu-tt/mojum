# Noosphere R0.1: bounded PUBLIC snapshot pagination

The optional moj​​um.noosphere_pagination.page_public_dataset() helper adds keyset pagination without changing the existing LocalNoosphere.dataset() API or SQLite schema.

## Usage

    from mojum.noosphere import LocalNoosphere
    from mojum.noosphere_pagination import page_public_dataset

    with LocalNoosphere("./memory.sqlite3", "dante", read_only=True) as memory:
        result = page_public_dataset(memory, topic="observation", page_size=100)
        consume(result["page"]["items"])
        cursor = result["page"]["next_cursor"]
        while cursor is not None:
            result = page_public_dataset(memory, cursor=cursor)
            consume(result["page"]["items"])
            cursor = result["page"]["next_cursor"]

## Contract

- SQL visibility is fixed to PUBLIC; there is no include_nonpublic parameter. PRIVATE and RESTRICTED rows cannot be requested through this API.
- Page one runs the canonical strict-wire/hash-chain verifier inside a read transaction and pins the journal event count and head. An optional independently retained expected_snapshot_head can be supplied.
- Each cursor binds the agent, pinned count/head, last returned sequence, page size and normalized filters. Its SHA-256 digest detects accidental modification but is not a MAC, signature or authorization token.
- Continuations re-verify the chain and require the pinned prefix to remain unchanged. Later appends are excluded. History shortening, corruption, cursor tampering, changed page size or changed filters fail closed.
- SQL keyset predicates use seq > last_seq and seq <= pinned_count; no OFFSET scan. Rows are fetched in bounded chunks, at most 1,000 returned per page.
- Source filtering follows duplicate-safe JSON decoding, matching existing Noosphere semantics.

## Costs and limits

The canonical chain is re-verified on every page, so integrity checking costs O(total journal events) per request even though page selection is keyset-based. This favors verifiable snapshot semantics over pretending that SQLite offers an immutable snapshot after the first connection transaction ends.

The helper requires an already-open LocalNoosphere(..., read_only=True) and rejects caller-owned SQLite transactions. It performs no writes, migration, file opening, networking or runtime wiring.

The digest is not authentication. PUBLIC is only the event visibility label, not proof of disclosure consent. This helper does not replace the separate Noosphere consent gate or PUBLIC export witness.

## Tests

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_noosphere_pagination.py' -v
