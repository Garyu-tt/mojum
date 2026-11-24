# Archive retention planner R0.1

## Purpose

mojum.archive-retention-plan/1 is a deterministic, advisory plan for sealed-segment retention. It identifies segments that may be presented to an operator for review. It never deletes, moves, opens, copies, or modifies archive files.

## Input contract

plan_retention(raw) accepts an exact dictionary using moj​​um.archive-retention-input/1 and explicit PUBLIC_SYNTHETIC consent. The inventory is bounded to 4,096 segments and 64 failure-domain labels per segment. Segment intervals must be contiguous, IDs unique, digests lowercase SHA-256, and the supplied previous/head chain internally consistent.

The caller supplies:
- a logical current tick and minimum age in ticks;
- a minimum recent-segment floor;
- a minimum number of distinct failure-domain labels;
- a trusted restore-anchor sequence/head;
- the expected final restore head.

The anchor must be genesis (sequence zero and the zero digest) or an exact verified segment boundary. All segments after the anchor are retained because they are required to replay to the declared restore head. Older segments are eligible for operator review only when they are old enough, verified, represented in enough distinct failure domains, and outside the recent-segment floor.

## Output and safety

Each segment receives KEEP or ELIGIBLE_FOR_OPERATOR_REVIEW, with stable reason codes. The report includes a SHA-256 digest over canonical JSON. Input objects are not mutated.

Unknown, malformed, unverified, too recent, under-replicated, or restore-required segments are kept. Eligibility is not a deletion instruction and must not be wired directly to a destructive command.

## Explicit limitations

- This is a pure metadata planner, not a filesystem/archive verifier.
- The caller's restore anchor and failure-domain labels are not authenticated here.
- A list of failure-domain labels does not prove physical independence, durability, or actual copy existence.
- No encryption, signatures, remote replication, restore operation, PRIVATE data access, or production archive handling is implemented.
- No wall clock is used; all age decisions use caller-supplied logical ticks.
- Independent exact-head CI and QA are required before acceptance.