# Mojum R0.8 — durable local write intent (no automatic recovery)

The R0.7 recovery audit proved that a journal one event ahead of its
authority registry is **ambiguous**: the extra Noosphere event could come
from an interrupted authorized operation or an out-of-band writer. A valid
hash chain alone does not authenticate who appended the event.

This experimental feature adds a **durable, canonical event-hash intent
committed to the trusted local authority registry *before*** the append.
The first state transition uses the existing authority row's **FROZEN**
phase with `pending_writer=NULL`. Consequently, other cooperating
`GuardedAquarium` mutators and authority transfers fail closed while the
intent is pending.

This is **single-host, cooperative** and is an optional sibling of
`GuardedAquarium` on the PR #88 stack. It does not claim two-SQLite
atomicity, distributed consensus or reliable unattended crash recovery.

## Three durable stages

<escape>1.</escape> **Prepare.** Compute the exact canonical Noosphere
event that a deterministic Aquarium `step()` or `acquire()` would
produce. Compute SHA-256 from current journal head and that event.
Validate the local writer, epoch, generation, prior head/count/tick.
Atomically insert one intent record into the existing *private*
LocalAuthority SQLite file and set the authority phase FROZEN.
The transaction commits **before** any Noosphere event append.

<escape>2.</escape> **Append.** Invoke the existing Aquarium mutation
pipeline. It still uses its own Noosphere CAS, appends exactly one real
PRIVATE event and advances local synthetic state. The intent remains
durable and authority remains FROZEN while this happens.

<escape>3.</escape> **Finalize.** Under a new authority-registry
transaction, verify the latest actual Noosphere head equals the
prepared event hash, the event count increased by exactly one, and
the expected tick is reached. Atomically advance authority
head/count/tick/generation, remove the prepared record and make
the writer ACTIVE again.

This guards against a second cooperating writer taking the same
authority while an intent is pending. The original mutator in
`GuardedAquarium` remains supported for the **unprepared** legacy
single-host experiments. An administrator must deliberately select
and provision this new mode, not silently upgrade an active process.

## Explicit installation and usage

First enroll the actual existing synthetic Aquarium journal and trusted
registry using the earlier PR stack. On the trusted writer service UID
(not root), opt into the table schema:

```bash
PYTHONPATH=src python -m mojum.write_intent install \
  --registry /var/lib/mojum/private/authority.sqlite3
```

Then use the new adapter in trusted Python code:

```python
from mojum.write_intent import IntentGuardedAquarium

with IntentGuardedAquarium(
    "/var/lib/mojum/private/dante.sqlite3",
    "/var/lib/mojum/private/authority.sqlite3",
    seed, graph,
    writer="node-a", epoch=1, generation=1,
) as organism:
    organism.step(dx=0.01)
    print(organism.generation)  # 2, after successful finalize
```

The existing `mojum.writer_service` still uses the legacy
`GuardedAquarium` adapter; this feature is **not yet the writer
daemon's default**. Integrating it with the daemon and running
new failure-injection tests there are separate reviewed work.

## Crash states and why they must STOP

The new read-only command:

```bash
PYTHONPATH=src python -m mojum.write_intent inspect \
  --journal /var/lib/mojum/private/dante.sqlite3 \
  --registry /var/lib/mojum/private/authority.sqlite3 \
  --organism-id dante-fixture-001 \
  --genome examples/genesis_seed.json \
  --graph examples/first_loop.json
```

Uses a WAL-consistent SQLite backup to a private scratch directory and
fully replays the deterministic synthetic Aquarium without changing the
source. Only schema, state and hash/count metadata are printed; actual
PRIVATE sensory events and acquisition IDs are not returned.

| Diagnostic result | Interpretation | CLI exit |
| --- | --- | --- |
| NO_PREPARED_INTENT | No staged operation exists | 0 |
| PREPARED_NO_EVENT_STOP | Intent committed but no journal event | 3 |
| PREPARED_EXACT_EVENT_REQUIRES_REVIEW | Exactly the declared event appears committed | 3 |
| PREPARED_MISMATCH_STOP | Journal differs from declared hash or count | 3 |
| JOURNAL_INVALID_STOP | Replay / journal verification failed | 3 |
| JOURNAL_CHANGED_DURING_AUDIT_STOP | Source moved while checking | 3 |
| UNTRUSTED_INTENT_STATE_STOP | Authority phase/epoch/generation disagrees with intent | 3 |

**Every report sets `auto_repair_allowed=false`.** A matching hash
shows *content equivalence*, not that the trusted writer executable
actually performed the operation. There is NO CLI that unfreezes a
failed intent, increments epoch or assumes that the process is the
rightful owner. After a crash, keep the writer service stopped and
preserve the original registry, journal, WAL and SHM sidecars for
independent forensic review. Never delete an intent record just
to let a process restart.

## Trust and limits

- The writer-registry file must be independently trusted and OS protected.
  A privileged attacker controlling the service UID or registry file
  can forge an expected intent and the corresponding Noosphere event.
  This is not a signed statement, TPM attestation or authenticated
  external witness.
- A separate `sqlite3` transaction in each DB means power loss between
  the commits can still produce an incomplete prepared operation.
  This protocol records the expected operation **before** that gap,
  making subsequent diagnosis stronger, but does not eliminate the gap.
- Two independent copies of the registry on disconnected devices
  can both prepare writes. No network authority/fencing/consensus
  is implemented. The existing reader-only lineage audit remains
  necessary when reconciling separate replicas.
- A direct privileged Noosphere writer can bypass the frozen registry;
  the next attempted prepared append then fails journal CAS or
  exact-hash verification and remains frozen.
- No general AI cognition, encrypted human memory, MuJoCo physics,
  Android runtime, hardware motor privilege or crash recovery automation
  is implied.
- This implementation uses the original deterministic synthetic
  Aquarium and supports a bounded history (10,000 journal records).
  No independent learned environment execution is claimed.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p 'test_write_intent_r01.py' -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Regression covers real committed frames, same-tick gene acquisition,
invalid-precommit input, after-prepare/no-event interruption, after-event/
before-anchor interruption, raw conflicting writer, transfer refusal,
corrupt record, cross-process audit CLI and scratch cleanup.

**Next:** independent review and a separate daemon integration behind
an explicit `--durable-intents` opt-in. Before any automatic reconciliation,
define a trusted operator/recovery capability that cannot be invoked
by ordinary clients and prove power-cut consistency with actual
separate SQLite WAL durability tests.
