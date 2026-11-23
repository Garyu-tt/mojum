# World trace minimizer R0.1 (issue #824, parent #7)

This is an **opt-in, offline, pure-stdlib QA aid** to reduce deterministic point-room fixture failures to a smaller reproducible move sequence. It is not MuJoCo, a physics authority, an execution capability, a signed attestation, or a replacement for the independent world trace validator of PR #808. It never opens files, uses networking, manipulates journals, handles PRIVATE data, or drives hardware.

## Usage

    from mojum.world_trace_minimize import loads_case, minimize_case

    case = loads_case('''{
      "schema":"mojum.world_trace_minimize/1",
      "data_classification":"PUBLIC_SYNTHETIC",
      "room":{"width":4,"height":4,"obstacles":[[2,1,0.01,2]]},
      "start":[1,2],
      "moves":[{"dx":0,"dy":0},{"dx":2,"dy":0},{"dx":0,"dy":0}],
      "witness":{"kind":"blocked"},
      "max_probes":192
    }''')
    result = minimize_case(case)
    assert result["witness_preserved"]
    print(result["replay_case"], result["complete"])

All input and output is in-memory data. Users can choose whether to persist a replay. No source path, output path, shell command, process, or host capability is accepted.

## Wire contract

Root fields: schema, data_classification, room, start, moves, witness, and optional max_probes. Classification MUST equal PUBLIC_SYNTHETIC; other classifications are rejected, not automatically redacted. The room has width, height, and obstacles (rectangles [x,y,width,height]). Start is [x,y]. Each move contains exactly dx and dy, both finite numbers.

The minimizer replays **every candidate** via the existing mojum.world_room.advance, from a fresh initial_state; all tick numbers and revisions are generated internally and never trusted from input.

Supported witness kinds:

- **blocked**: at least one move was blocked.
- **blocked_at_position**: at least one move was blocked with the state remaining at the specified exact position.
- **end_position**: the final state equals the specified exact position.

The latter two need a position field containing [x,y]. Positions compare exactly, not within epsilon. Floating point addition can change last-bit results; use simple exact-binary coordinates when possible. An original trace with an absent witness is rejected.

## Bounded deterministic reduction

The algorithm greedily removes contiguous chunks, then performs left-to-right single-move deletion cleanup. It returns original source indices in order, a complete in-memory replay case, replay final position, observed blocked-event count, evaluated probes/ticks, and SHA-256 source/replay checksums over stable canonical JSON. These are **checksums, not authenticated signatures**.

With complete=true, the result is **1-minimal**: removing any one remaining movement would lose the witness. This does not prove the globally shortest counterexample. With complete=false, a probe/tick budget was exhausted: the returned trace still preserves the witness, but minimality was not established.

Limits: 64 KiB JSON input, 128 moves, 128 obstacles, max_probes 1..256 (default 192), a total maximum of 16,384 evaluated movement ticks, room/move numeric magnitudes at most 1000 metres, and each movement length at most 1000 metres. Unknown fields, Boolean numeric values, NaN/Infinity, duplicate JSON keys, oversized or invalid geometries and PRIVATE inputs fail closed.

No wall clock, randomness, hardware access, external dependencies, or changes to canonical world state.

## Regression test

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_trace_minimize.py' -v

The new suite exercises causal prefixes, thin obstacles, replay stability, object isolation, empty traces, unattainable witness, invalid numerics/classification, duplicate keys, and bounded incomplete results.
