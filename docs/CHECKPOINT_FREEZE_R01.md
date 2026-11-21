# Mojum checkpoint freeze R0.1 — one local Aquarium and SQLite journal

This is a cooperative local checkpoint consistency barrier, stacked on
Aquarium cold restore PR #52 (and its dependencies #48, #46, #44). It is
not multi-device consensus or persistent fault tolerance.

## Problem

Previously, the cold exporter compared the journal head and Aquarium
checkpoint before/after copying, but another writer could append while the
GENESIS, shadow learner and world snapshots were being read. Post-checks
could detect some races, but did not reserve a stable local write revision.

## Local barrier contract

The new Aquarium.freeze_checkpoint() context manager:

1. Takes a per-instance reentrant mutation lock used by step(), acquire(),
   checkpoint(), and close(). A nested freeze is refused.
2. Acquires a SQLite BEGIN IMMEDIATE writer reservation on the same Noosphere
   database. Other local SQLite connections cannot commit new events until
   release. A lock timeout fails closed.
3. Verifies that the Aquarium's last locally committed journal head matches
   the database head; stale instances cannot claim newer committed history.
4. Verifies the event hash chain, recording a pinned checkpoint/count/head.
   The cold exporter serializes all five component bytes under this barrier.
5. At exit, compares checkpoint, database head and count, then releases the
   SQLite transaction by rollback and frees the process lock even on errors.

Calling step() or acquire() during a freeze raises AquariumError before an
append. Normal writes resume after exit. A stale instance trying to append
through a different connection now uses its last-known head as expected CAS,
not whichever new head happens to exist in SQLite.

The cold exporter, capture_quiescent_aquarium(), automatically wraps the
entire component read in this context.

## Usage on a synthetic source

    from mojum.aquarium import Aquarium
    from mojum.cold_aquarium import capture_quiescent_aquarium

    with Aquarium("./dante-source.sqlite3", seed, graph) as aquarium:
        for _ in range(20):
            aquarium.step()
        with aquarium.freeze_checkpoint() as pinned:
            # Read-only metadata; step/acquire are blocked here.
            head = pinned["journal_head"]
            tick = pinned["checkpoint"]["tick"]
        # The exporter enters its own barrier:
        bundle = capture_quiescent_aquarium(aquarium)

Do not invoke capture_quiescent_aquarium() while already inside another
freeze context; recursive freezes are deliberately refused.

## Limits and safety

- Only coordinates one Aquarium instance and its single SQLite journal.
  It does not freeze MuJoCo, external game worlds, GPU workers, other
  in-memory runtimes or foreign databases.
- SQLite BEGIN IMMEDIATE is a temporary local write reservation; it is not a
  signed ownership token, lease, distributed consensus or fencing epoch.
- Separate processes can fork the organism after capture. They require a
  distinct, later single-writer authority and reconciliation design.
- No encryption, authenticated source, device pairing, durable anti-rollback,
  cross-device power-loss atomicity, network transport or automatic failover.
- The body/motor state remains disabled on import, never clearing e-stop.
- Checkpoint bundles contain PRIVATE Noosphere event history in plaintext
  Base64; protect them and use an independently trusted manifest fingerprint.
- GitHub Actions tests Linux Python runtimes, not Android/Pixel 9 hardware.

## Regression

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_checkpoint_freeze_r01.py' -v
    PYTHONPATH=src python -m unittest discover -s tests -v

Tests include blocked writes, second SQLite writer contention, nested freeze
refusal, safe close behavior, abort cleanup, stale head refusal, exact private
capture and restart after release.

Next: durable per-organism authority epochs, trusted anchors, and explicit
fork/conflict policies before any network replication experiment.
