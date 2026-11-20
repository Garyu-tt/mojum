# Guarded Aquarium R0.1 — opt-in locally fenced mutation gateway

This **CPU-only, single-host cooperative prototype** binds normal synthetic
Aquarium `step()` and `acquire()` calls to the previously added durable local
authority registry in PR #67. It stacks on #67 and does not modify an upstream
branch, `main`, the peer-5 epoch contract or the separate motor-learning PRs.

## What changes

Use `mojum.guarded_aquarium.GuardedAquarium` instead of calling the raw
`Aquarium` API when testing authority-aware mutations.

Every supported mutation executes in this order:

1. Require both existing regular SQLite files, their original device/inode
   identities and trusted parents. **Neither database is auto-created**.
2. Take a registry `BEGIN IMMEDIATE` reservation, serializing other
   cooperative guards, progress updates and ownership-transfer transactions.
3. Validate the registry is ACTIVE for exactly the stated writer, epoch and
   generation. Check that the local Aquarium replay's own last-known head,
   live SQLite head, event count and tick equal the registry anchor.
4. Call exactly one ordinary `Aquarium.step` or `Aquarium.acquire`, which
   commits one Noosphere event using the existing CAS semantics.
5. Verify the journal head has changed, exactly **one** event was appended,
   and the expected tick moved (+1 for step, +0 for acquisition).
6. Update the registry's anchored head/event count/tick and increment its
   generation under the same registry transaction, then commit that
   transaction. Other cooperative writers cannot pass its gate during this
   sequence.

A failed registry admission, stale writer, frozen handoff, wrong generation,
missing/replaced registry, or unavailable/locked registry fails closed before
the journal mutation. The same gate protects a read-only checkpoint check
and `export_cold()` of PRIVATE state.

## Python example

Assume an existing Aquarium SQLite journal has already been captured as a
PRIVATE cold archive and explicitly provisioned in a trusted shared registry
using `mojum.local_authority` from PR #67.

```python
from mojum.guarded_aquarium import GuardedAquarium

with GuardedAquarium(
    "./dante-source.sqlite3",
    "./local-authority.sqlite3",
    seed, graph,
    writer="node-a", epoch=1, generation=1,
) as organism:
    organism.step(dx=0.01)
    # One accepted mutation increments generation by exactly one.
    print(organism.generation)  # 2
    print(organism.checkpoint()["tick"])
    private_manifest = organism.export_cold("./new-private-archive.json")
```

To transfer local ownership, first produce equivalent cold snapshots on
both sides. The same trusted registry is then updated through
`LocalAuthority.freeze_transfer` and `activate_transfer`. Old owners are
rejected on their very next guarded operation. Neither the source nor the
destination is automatically promoted based on a larger epoch or tick alone.

## Critical crash and security boundaries

**Not atomic across two SQLite databases.** The journal append completes
before the registry anchor commit. If the process is interrupted during that
gap, the journal may contain a newer event than the registry. This design
deliberately does **not** advance the stored anchor automatically on restart:
the guard becomes poisoned after a partial failure and a newly opened guard
refuses the unmatched journal. Recovery needs an independent review of
Noosphere ancestry, the original admission, and authority continuity.

**No global enforcement of the raw API.** A process with direct writable
access to `Aquarium`/Noosphere can bypass this gateway entirely. Such writes
cause the next guarded admission to fail because the journal head/count does
not match, but they are not prevented. For real enforcement, filesystem/OS
permissions must restrict direct writes and route all mutations through a
single trusted service, or use an equivalent capability boundary.

**Not multi-device consensus.** Two disconnected machines using cloned
registries can both admit writers. In that situation this local gateway
cannot prevent split-brain. No authenticated node identity, signed transfer,
distributed lease, witness quorum, automatic leader election or remote
hardware fencing exists. The writer labels are validated strings only.

The registry file's identity is checked before and during a guarded call.
This protects against ordinary missing/replaced local files but is not
race-free protection against a hostile process able to replace parent
directories or manipulate SQLite journals. Keep both files on trusted local
storage. Raw filesystem access is outside this prototype's threat model.

No motor authority, MuJoCo actuator, emergency-stop reset, external-world
control or secrets are granted by this adapter. Never copy PRIVATE journal
data to a public issue, CI artifact or unencrypted untrusted transport.

## Test

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_guarded_aquarium_r01.py' -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Regression cases include automatic committed head/revision anchoring for
steps and acquisitions, wrong/stale identities and epochs, two competing
local handles, transfer freeze and named target activation, missing/unlinked
or replaced registry, partial commit poisoning, lock contention, private
export, detected raw-API bypass and metadata-only reporting.

**Next engineering step:** route *all* state mutation capabilities through
one trusted process and restrict direct write access, then design an
explicit crash reconciliation workflow and real distributed authority
witnesses before attempting network partition/failover tests.
