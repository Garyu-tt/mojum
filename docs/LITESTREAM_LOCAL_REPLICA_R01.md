# Litestream local SQLite WAL replica R0.1 (issue #752, parent #693)

**Status:** opt-in standalone POSIX prototype. Default Mojum Aquarium, Noosphere SQLite writer, Borg PR #729, scheduler, network and graph IR remain unchanged. No new mandatory dependency. Not a production backup service.

## Upstream

Litestream **v0.5.18** (2026-11-03), Apache-2.0.
Source and release: https://github.com/benbjohnson/litestream/releases/tag/v0.5.18
License: https://github.com/benbjohnson/litestream/blob/main/LICENSE
Commands: https://litestream.io/guides/file/ and https://litestream.io/reference/restore/

The backup consists of WAL/LTX replica data, NOT a directly usable SQLite database. Litestream 0.5 removed native age encryption. A local replica contains plaintext unless stored on an encrypted filesystem. A replica on the same physical disk does not protect against disk loss. WAL replication does not authorize multi-writer Noosphere journals.

## Local synthetic fixture

Install and independently verify the upstream v0.5.18 binary first. The wrapper does not download software. In a POSIX shell from the repository root, prepare PRIVATE directories:

    mkdir -m 700 -p /tmp/mojum-ls-demo/private /tmp/mojum-ls-demo/replica

Use Python stdlib sqlite3 to create a disposable source.db in the private directory with a table and PUBLIC_SYNTHETIC records; chmod source.db to 0600. Then validate paths and run a bounded explicit replication:

    python3 tools/litestream_local.py plan --source /tmp/mojum-ls-demo/private/source.db --replica-dir /tmp/mojum-ls-demo/replica
    python3 tools/litestream_local.py replicate --binary /usr/local/bin/litestream --source /tmp/mojum-ls-demo/private/source.db --replica-dir /tmp/mojum-ls-demo/replica --seconds 10

The replica is local file:// only. All paths must be absolute without symlinks/traversal. The binary must have version 0.5.18; optionally provide --binary-sha256 with an independently verified SHA-256. The wrapper starts no persistent daemon, cloud transport or auto-download. An OS firewall/sandbox is still required to *guarantee* no network for a malicious/extended external binary.

**Important:** real Litestream can checkpoint the source SQLite WAL; this replication is NOT a read-only observation of a live database. Before any live run, separately approve its interaction with the canonical single-writer Noosphere and backup policy.

## Restoring without overwriting

Restoration requires a separately trusted SHA-256 of the EXACT restored SQLite image. A raw file hash from a live, actively written WAL source is NOT a valid expectation for the restored image; neither is an untrusted hash from the replica itself.

    python3 tools/litestream_local.py restore --binary /usr/local/bin/litestream --replica-dir /tmp/mojum-ls-demo/replica --output /tmp/mojum-ls-demo/private/recovered.db --expected-sha256 YOUR_INDEPENDENT_EXACT_RESTORED_IMAGE_SHA256 --timeout 30 --max-bytes 134217728

The target must not exist. Restore runs in a 0700 temporary staging folder on the same volume as the destination, with POSIX resource limits and a bounded timeout. The candidate SQLite image undergoes full integrity_check and SHA-256 verification, then is published with a no-clobber hardlink at mode 0600. On failure, no target is published. Never replace a live journal with it. The resulting image requires separate trusted Noosphere head/hash-chain and deterministic replay verification.

## Tests and acceptance

Run:

    python3 -m unittest discover -s tests -p test_litestream_local.py -v

14 local offline stdlib synthetic tests passed: version/digest checks, path/symlink/permission denial, abnormal process exit, timeboxing, no-clobber restore, bad anchor refusal and cleanup. Tests use a fake executable, not installed real Litestream.

**Pending:** native v0.5.18 smoke, real WAL/disk failure and partition recovery, CPU/RSS x86_64/ARM64 benchmarks, independent trusted snapshot anchors, encryption at rest, physical replica isolation, OS network confinement, and exact-head CI plus independent QA. Limits are per-file/process rather than aggregate filesystem quotas.

## Borg coexistence

Borg cold-archive PR #729 is a distinct, already claimed integration. This #752 incremental WAL slice neither modifies it nor claims shared guarantees or production deployment. Independent reviewers must reconcile interfaces before any integrated recovery runbook.
