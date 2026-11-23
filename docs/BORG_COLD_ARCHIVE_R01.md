# Mojum #693: opt-in Borg 1.4.5 encrypted cold archive (R0.1)

**Status:** experimental backup adapter, not a release, not a live failover system. Code: `src/mojum/borg_cold_archive.py`; tests: `tests/test_borg_cold_archive.py`. Default Python 3.11+ stdlib-only Aquarium is unchanged.

## Components and versions

- The pre-existing `mojum.aquarium_cold_copy.export_cold_copy` takes a SQLite online backup of committed WAL pages and performs full synthetic Aquarium replay.
- The new opt-in adapter sends **that self-contained copy**, never the live SQLite+WAL set, to **BorgBackup 1.4.5**. The version is deliberately pinned to 1.4.5 and checked before any backup. Borg is installed and configured by an operator, never automatically downloaded by Mojum.
- Borg upstream: https://github.com/borgbackup/borg (BSD-3-Clause). Check actual upstream LICENSE, build artifacts, package transitive licenses and repository key management before production use.
- Litestream upstream: https://github.com/benbjohnson/litestream (Apache-2.0, current 0.5 series). **Not wired in this PR.** Its continuous WAL replication, remote replicas, credentials and actual RPO testing remain a separate review. This adapter does not claim Litestream support or automatic copies.
- Dependency matrix status remains `planned`; a tested optional spike is not release acceptance or license clearance.

## One-host opt-in use

Prerequisites: POSIX host, trusted initialized **local** Borg v1.4.5 encrypted repository, owner-only directory, secure Borg key/passphrase made available out of band (Borg's own `BORG_PASSCOMMAND`/`BORG_PASSPHRASE` configuration). Never put a passphrase in shell arguments, journal, JSON output or a PR. This adapter refuses unauthenticated `none` or integrity-only `authenticated` repositories, remote repository syntax, non-POSIX hosts and all Borg versions other than 1.4.5.

With an existing Aquarium journal, seed and graph matching its bootstrapped identity:

```sh
PYTHONPATH=src python -m mojum.borg_cold_archive archive \
  --db /private/active.sqlite3 \
  --repo /private/encrypted-borg-repo --archive sample-001 \
  --genome examples/genesis_seed.json --graph examples/first_loop.json \
  --agent-id dante-fixture-001
```

The result prints only event count, tick, SHA-256 and hash-chain head, never PRIVATE payloads. **Protect the returned `journal_head` and `db_sha256` in an independent trusted location outside the Borg archive/repository.** A hash stored alongside a compromised archive does not authenticate it. This adapter never claims private key rotation, identity binding across hosts or encrypted temporary plaintext in memory/disk.

To verify and restore into a **new** file only:

```sh
PYTHONPATH=src python -m mojum.borg_cold_archive restore \
  --repo /private/encrypted-borg-repo --archive sample-001 \
  --output /private/recovery/restored.sqlite3 \
  --genome examples/genesis_seed.json --graph examples/first_loop.json \
  --agent-id dante-fixture-001 \
  --expected-head YOUR_INDEPENDENT_64_HEX_HEAD \
  --expected-sha256 YOUR_INDEPENDENT_64_HEX_DB_SHA256 \
  --expected-tick 9
```

Restore extracts the expected member into a private temporary directory, rejects symlinks and over-budget files, checks the independently anchored file hash, replays against GENESIS identity and graph, and publishes the file with exclusive hard-link semantics. Existing destination and SQLite sidecars are never overwritten. Borg `check --verify-data` and an actual restore/replay run after archive creation. If one of these steps fails, an incomplete archive may exist and **must not be used as a passing backup**; manually audit/clean it.

## Threat model, limitations and acceptance gates

- Cold backup only. The live source can continue committing. The saved tick/head describes the instant of the SQLite consistent snapshot; no cross-component atomic commit, replica quorum, active-active SQLite, single-writer fencing or automatic failover is provided.
- Private plaintext exists temporarily in a mode-0700 working directory and later at the explicitly restored output path (mode 0600 on POSIX). Borg encrypts repository archive contents, **not the source journal, temporary copy, swapped pages, swap space or chosen restored path**. Trusted local host required.
- Borg CLI is launched without a shell with a bounded timeout; its stdout/stderr are suppressed on failure so paths and secrets are not echoed. Borg may still generate trusted local logs/caches. Explicit local repo may itself be on a filesystem managed by an operator, and this adapter does not certify physical host isolation.
- The returned digest and head must be anchored independently to resist a local adversary who can rewrite both the archive and its metadata. A single archive is not two independent recoverable replicas. Two archives inside the *same* Borg repository do not qualify either.
- Retention expiration, mid-write host crash, Borg key loss, real Borg binary/OS profiling, disk-full cases, restore across hosts, independent two-copy recovery, RPO and RTO **remain unproven**. Do not use on production PRIVATE histories until independently tested and security-reviewed.
- Offline stdlib fixture remains unchanged; all Borg operations are explicit opt-in, and import does not start a subprocess.
- The isolated unit tests use a controlled fake Borg CLI for contract/failure checks. They **do not prove** real Borg encryption/restore correctness. A separate real-Borg pinned binary CI run and independent exact-HEAD QA are required before merge.

Run:

```sh
PYTHONPATH=src python -m unittest tests.test_borg_cold_archive -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_aquarium_cold_copy.py' -v
```
