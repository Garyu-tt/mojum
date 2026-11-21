# Mojum R0.5 — cross-UID local writer transport (Linux)

This is an **opt-in, stricter local operating-system boundary** for the
single-process writer service from PR #76. It stacks on that PR without
changing its shared parent branch or `main`.

The default mode is unchanged: a private `0700` owner socket directory,
a `0600` socket and Linux `SO_PEERCRED` accepting the service's own UID.

With both new arguments `--client-uid` and `--client-gid`, the service
requires a **different unprivileged client UID** from the unprivileged
service UID. It permits the configured group to traverse one `0710`
socket directory, sets the Unix socket's owning group and `0660` mode,
then checks the peer's **kernel-provided UID** before processing a request.

The group grants *transport reachability* only; having a supplementary
group membership does NOT grant writer authorization unless the peer UID
also matches `--client-uid`. Only one specific client UID is enrolled.
This checks Linux OS identity, not a specific binary, application signature,
physical device identity or an authenticated network node.

## Data isolation

In cross-UID mode, these startup checks are mandatory:

- The SQLite journal and local-authority registry must **already exist**,
  be regular non-symlinked files, be owned by the **service UID**, be
  owner readable/writable and have **no group/other permission bits**.
  In practice use `0600`.
- Both parent directories must already exist, be service-UID owned,
  non-symlinked absolute paths, and have no group/other bits
  (typically `0700`).
- The private archive directory is similarly owner-only `0700`.
  Private Noosphere archives are NOT served as downloadable RPC payloads.
- The socket directory must be owned by the service UID, have the
  configured socket group and exactly `0710` permissions.
  It cannot be a data directory.
- The service process must already belong to the client socket group.
- Startup and every writer-service accept cycle validate these constraints.
  A changed, missing or exposed data directory/file stops request handling
  **before** accepting another RPC. The socket inode is also pinned against
  ordinary path replacement.
- It never calls `chmod` on a journal or registry to "repair" unsafe
  permissions automatically: unsafe inputs fail closed.

## Suggested deployment layout (illustrative, NOT performed here)

Example Linux identities:

- service account: `mojumwriter` (unprivileged; owns databases)
- client account: `mojumclient` (different UID; **cannot** traverse data
  directory, but belongs to the socket access group)
- socket group: `mojum-ipc`

Paths:

| Object | Owner | Group | Mode |
|---|---|---|---|
| /var/lib/mojum/private/ | mojumwriter | mojumwriter | 0700 |
| /var/lib/mojum/private/dante.sqlite3 | mojumwriter | mojumwriter | 0600 |
| /var/lib/mojum/private/authority.sqlite3 | mojumwriter | mojumwriter | 0600 |
| /var/lib/mojum/private/exports/ | mojumwriter | mojumwriter | 0700 |
| /run/mojum-writer/ | mojumwriter | mojum-ipc | 0710 |
| /run/mojum-writer/writer.sock | mojumwriter | mojum-ipc | 0660 |

The source directory and Python package must be readable by the service
account. Provision the original PRIVATE journal and registry through a
reviewed, explicit onboarding process under the service UID first. No
auto-creation of an authority registry is permitted at service startup.
Do not copy a trusted registry to an isolated second device and claim
it provides distributed fencing.

An administrator can use `systemd-sysusers` / `systemd-tmpfiles` or
explicit OS account provisioning to prepare this layout. For example, a
tmpfiles.d rule for the runtime socket directory is:

```ini
d /run/mojum-writer 0710 mojumwriter mojum-ipc -
```

The service must be started with `User=mojumwriter`, an appropriate primary
group and `SupplementaryGroups=mojum-ipc` (or equivalent). It must not run
as root. The client account must also belong to `mojum-ipc`. Keep clients
out of the `mojumwriter` account and out of database/archive directories.
Use root/administrator privileges **only for installing ownership/modes**,
never to run the app as root.

Example launch under the configured service account:

```bash
PYTHONPATH=/opt/mojum/src python3 -m mojum.writer_service serve \
  --socket /run/mojum-writer/writer.sock \
  --db /var/lib/mojum/private/dante.sqlite3 \
  --registry /var/lib/mojum/private/authority.sqlite3 \
  --archive-dir /var/lib/mojum/private/exports \
  --genome /opt/mojum/examples/genesis_seed.json \
  --graph /opt/mojum/examples/first_loop.json \
  --writer node-a --epoch 1 --generation 1 \
  --client-uid 1102 --client-gid 1103
```

The numeric IDs are **illustrative**; substitute IDs actually provisioned
on the host and independently verify them. This command must be executed as
the dedicated service account, not as root. The client uses the unchanged
`mojum.writer_service request` CLI as its distinct OS user.

## Security and crash limits

**This PR does not install accounts, modify host permissions, or deploy.**
The Python CI suite tests rejection of unsafe modes/paths and the Linux
kernel-provided peer UID on real Unix sockets, but does **not** obtain a
second real OS account on the runner to prove a successful cross-UID request.
That requires a separately authorized end-to-end host test with distinct
user accounts (and audit of ACLs, POSIX groups, default umask and SQLite
WAL sidecars).

Running an untrusted client as the **same UID** as the service defeats
filesystem-based separation. Root or an administrator may also bypass it.
A compromised allowed client UID can send any supported `step` or
`acquire` request within the existing Aquarium validation contract.
This protocol deliberately does not promise app-specific consent.

More importantly, this is STILL **single-host local control**, not
distributed quorum/lease consensus: network partitions between cloned
registry files can fork the organism. Separate SQLite registry and journal
commits are not crash-atomic, and crash recovery still fails closed pending
manual reconciliation. Real MuJoCo motor control, emergency-stop override,
encrypted memory and live migration are outside this synthetic fixture.

## Regression

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_writer_uid_separation_r01.py' -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Tests cover configured distinct non-root client UID, group membership,
strict `0710` socket directory, kernel same-UID denial, socket `0660`
ownership, private `0600` data files, `0700` source/archive directories,
symlink refusal, file-mode deterioration after server start, and absent data
rejection. Existing PR #76 same-UID compatibility tests continue unchanged.

Next review steps: actual two-unprivileged-UID deployment smoke, mandatory
OS privilege separation on supported Linux packages and a distinct trusted
identity/enrollment layer before pursuing distributed authority.
