# Mojum local authority handoff R0.1: single-host coordination

This feature implements a persistent SQLite cooperative local writer-admission
protocol. It does not solve multi-device consensus or safety during a network
partition. It stacks on PR #62 and does not edit peer-5 fencing PR #31/#39.

## State model

One trusted registry file stores, per organism:
- phase: ACTIVE or FROZEN
- writer: syntactically validated node label, NOT a security identity
- pending_writer: proposed receiver while FROZEN
- epoch: monotonically increasing local ownership generation
- generation: persisted compare-and-swap counter
- head, event_count, tick: committed Noosphere journal anchor

All transitions use a SQLite BEGIN IMMEDIATE transaction on the single
shared trusted registry file. A missing registry is never auto-created by
ordinary client commands. Explicit provision is required.

Provision pins validated cold journal evidence and the initial writer.
Admit checks the current writer/epoch/generation, head, count and tick.
Progress accepts only a strictly extended journal whose old prefix hash
still matches the pinned registry head. No full-event merge is performed.

Freeze_transfer requires source and destination archives with identical
verified journal histories, and the source must be the currently admitted
writer. It durably changes the local record to FROZEN, denying ALL new
cooperative admissions. Activate_transfer checks the pending target,
epoch, generation and exact unchanged history, then increments the epoch
and grants a new ACTIVE local admission to the target.

If the transfer is interrupted after freeze, there is no timeout and no
automatic rollback or promotion. A frozen record remains FROZEN pending
a separately reviewed recovery procedure. This is intentional fail-closed
behavior, not high availability.

## Offline reproduction

Prepare the same trusted synthetic cold archive on source and target.
Keep the real archive PRIVATE: its Base64 fields include full private memory.
Get the manifest fingerprint through a separate trusted channel.

    ARCHIVE=./dante-cold-r30.mojum.json
    REGISTRY=./local-authority.sqlite3
    DIGEST="trusted-64-character-lowercase-manifest-hash"

    PYTHONPATH=src python -m mojum.local_authority provision \
      --registry "$REGISTRY" --organism-id dante-fixture-001 \
      --archive "$ARCHIVE" --tick 30 --manifest-sha256 "$DIGEST" \
      --writer node-a

    PYTHONPATH=src python -m mojum.local_authority freeze \
      --registry "$REGISTRY" --organism-id dante-fixture-001 \
      --archive "$ARCHIVE" --tick 30 --manifest-sha256 "$DIGEST" \
      --target-archive "$ARCHIVE" --target-manifest-sha256 "$DIGEST" \
      --source-writer node-a --target-writer node-b \
      --epoch 1 --generation 1

    PYTHONPATH=src python -m mojum.local_authority activate \
      --registry "$REGISTRY" --organism-id dante-fixture-001 \
      --archive "$ARCHIVE" --tick 30 --manifest-sha256 "$DIGEST" \
      --target-writer node-b --prior-epoch 1 --frozen-generation 2

    PYTHONPATH=src python -m mojum.local_authority admit \
      --registry "$REGISTRY" --organism-id dante-fixture-001 \
      --archive "$ARCHIVE" --tick 30 --manifest-sha256 "$DIGEST" \
      --writer node-b --epoch 2 --generation 3

Try the old writer node-a with epoch=1/generation=1 after transfer; it
must be denied. CLI outputs metadata but no private journal events.

## Critical limitations

- Two machines with independent copies of the registry can both be ACTIVE.
  Do NOT distribute cloned registries and treat this as mesh leader election.
- Admission is a point-in-time cooperative gate, not a signed bearer token,
  network lease, durable witness quorum or cryptographic capability.
- The Python API does not replace or guard direct Aquarium.step calls.
  A client that can write its Aquarium SQLite journal without consulting
  the registry bypasses this protocol. Truly safe local writing requires
  enforcement at EVERY mutation boundary with the same trusted registry.
- Transactional CAS protects this one SQLite registry, not atomicity with a
  separate Aquarium journal, MuJoCo, another device or external memory store.
- A local crash between journal mutation and progress recording can leave
  the registry behind. Fail closed and reconcile explicitly; never skip
  history or infer authority from a larger tick/epoch alone.
- A missing or inaccessible registry denies admission by default.
  This intentionally favors safety over availability.
- Node labels are syntactically checked but not cryptographic identities.
  A valid SHA chain or independently pinned checksum is not a signature.
- No network communication, automatic failover, signing, encryption,
  peer enrollment, device attestation or split-brain arbitration.
- Body/motor authority and emergency-stop overrides are not restored.
- CI verifies a shared local SQLite file on Linux, not Pixel 9 hardware.

## Tests

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_local_authority_r01.py' -v
    PYTHONPATH=src python -m unittest discover -s tests -v

Regression tests cover reopening durable state, two independent SQLite
handles, freeze denial, epoch increment, stale generation replay, wrong
writer IDs, strict ancestry progress, divergent branches, unrelated
bootstraps, no timeout promotion, missing registry, private file modes
and CLI handoff across separate Python processes.

Next step: add authenticated enrollment and a universally enforced
writer-fencing boundary, then test unavailable-witness network partition
behavior and crash-consistency across the control and data journals.
