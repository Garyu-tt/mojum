# Spartan model-flight audit R0.1 (issue #478)

The dispatcher in PR #477 is **not** proof that 300 model API calls are happening. This standalone, stdlib-only audit tool consumes a bounded provider-boundary JSONL log and asks a narrower, testable question: **how many distinct workers had completed, successful model calls overlapping in collector time?** It has no API token, GitHub write permission or worker execution capability.

## Collection boundary and proof level

- Log records must be emitted by a **separately operated, trusted provider/gateway instrumentation process** at actual outbound call start and verified provider response; never let model-generated code write to this evidence log.
- The at_ms field is the monotonic millisecond timestamp assigned by the **same collector clock**. Do not merge unsynchronized worker wall clocks into one file. Events must be in nondecreasing collector order. Half-open [start, finish) intervals ending and starting at the same instant do not overlap.
- Each request_id is globally unique within a log. A successful finish must contain an actual, unique upstream response_id. A start without a matching finish, an error, or 300 claimed SQLite leases does **not** count toward the successful concurrency peak.
- Valid syntax, matched IDs and high concurrency in *a file* are **not** cryptographic evidence that a provider made those calls. Independently protect and authenticate the collector and compare against provider invoices / usage API before claiming real production throughput or cost. This tool does not create 300 workers, cap provider spending or enforce billing. Provider-side meter remains authoritative.

JSONL schema (no prompts, API keys, response text, credentials or other fields permitted):

    {"at_ms":1000,"kind":"start","request_id":"request-1","worker":"nodeA.1","model":"gemini-3.5-flash-lite"}
    {"at_ms":1001,"kind":"start","request_id":"request-2","worker":"nodeB.2","model":"gemini-3.5-flash-lite"}
    {"at_ms":1120,"kind":"finish","request_id":"request-1","worker":"nodeA.1","model":"gemini-3.5-flash-lite","outcome":"ok","response_id":"provider-1","input_tokens":100,"output_tokens":50,"cost_usd":"0.00032"}
    {"at_ms":1121,"kind":"finish","request_id":"request-2","worker":"nodeB.2","model":"gemini-3.5-flash-lite","outcome":"error","input_tokens":0,"output_tokens":0,"cost_usd":"0"}

An error finish **omits** response_id. Numeric usage fields must be nonnegative integers and USD amounts decimal **strings** to avoid floating-point rounding. An incomplete bill cannot be treated as zero, so collectors should only fill accounting values from actual provider meter information; otherwise log separately and mark the dataset non-auditable.

    python3 tools/spartan_inflight_audit.py --input ./PRIVATE-provider-events.jsonl --target 300 --require-target
    python3 -m unittest discover -s tests -p 'test_spartan_inflight_audit.py' -v

Exit 0: structurally valid log and, with --require-target, target observed among completed successful calls. Exit 1: valid telemetry but target not yet reached. Exit 2: missing, malformed, impossible or oversized evidence. successful_peak.peak_distinct_workers is the conservative metric for 300 Spartans; completed_peak_including_errors and unfinished help diagnose unsuccessful calls, but do not prove success. In either case the output is explicitly marked as untrusted until independently sourced.

Limits: default 100,000 events, max adjustable to 1,000,000, maximum 4,096 bytes per JSONL line, worker ID bounded, decimal cost per operation bounded. This is a post-hoc bounded audit, not a high-throughput streaming telemetry service or security boundary.
