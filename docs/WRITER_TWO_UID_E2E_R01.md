# Mojum QA R0.6 — real Linux multi-UID writer integration

This change extends the **review-only** single-host writer separation
PR #81. It adds an automated smoke test on a **fresh, disposable
GitHub-hosted Ubuntu VM**. No external VM, workstation, NAS,
Proxmox host or persistent service is changed.

## What gets exercised

A dedicated workflow creates three **real Linux identities**, plus one
Unix socket access group, only inside the temporary CI runner:

| Account | Effective identity and purpose |
| --- | --- |
| `mojumqawriter` | unprivileged writer daemon, owns data and authority registry |
| `mojumqaclient` | allowed client UID; can connect through shared IPC group |
| `mojumqastranger` | *not* allowed client UID, but deliberately in same IPC group |
| `mojumqaipc` | socket transport group (not an authority grant) |

The tests use Python's POSIX `subprocess` UID/GID drop parameters.
They do not substitute an arbitrary JSON `client_id` header. Linux
`SO_PEERCRED` provides peer credentials to the running daemon.

For every case the ephemeral test runner prepares:

- Private service-owned `0700` data directory containing actual SQLite
  Noosphere and LocalAuthority databases created as the writer UID.
- Distinct service-owned `0710` socket directory in the `mojumqaipc` group.
- Private `0600` database files and `0700` private archive directory.
- Existing, explicitly provisioned trusted local journal state. No
  implicit re-provisioning or auto-reset.

### Acceptance cases

1. A **real separate client UID** can query status and commit one
   synthetic Aquarium frame through the Unix socket. The tick, journal
   head and authority generation advance coherently.
2. That client cannot open the Noosphere SQLite file, LocalAuthority
   registry, PRIVATE bootstrap archive, or private export directory:
   the **Linux kernel returns PermissionError** when the test directly
   invokes `os.open` under the client's real UID.
3. A stranger in the same Unix socket group, and even the writer
   service's own UID, are refused by the RPC peer-UID check. Group
   membership grants filesystem reachability only.
4. The allowed client can request an archive export, but cannot read
   the resulting PRIVATE archive file directly.
5. After a real daemon `SIGKILL`, the old Unix socket pathname remains
   and a new daemon refuses to hijack it. The test operator checks the
   dead process, existing socket inode, owner and mode before explicitly
   cleaning that path. Restart at the **actual committed generation**
   works; stale generation is refused.
6. A deliberate out-of-band journal append under the *privileged writer
   service UID* causes the next guarded service startup to fail closed:
   the journal head no longer matches the persisted authority anchor.
   The registry is not silently advanced.

All tests use fresh synthetic fixture memory, not real personal data.
Linux root privileges exist **only inside the hosted CI job** for
creating temporary test accounts and launching child processes under
different UIDs. GitHub Actions permissions are reduced to
`contents: read`, and no long-term credentials are requested.

## How to run

The workflow is `.github/workflows/writer-two-uid-e2e.yml`, enabled
for PRs and branch pushes. Its job name is `linux-distinct-uid`.
The dedicated regression is:

```bash
sudo groupadd --system mojumqaipc
for name in mojumqawriter mojumqaclient mojumqastranger; do
  sudo useradd --system --no-create-home --user-group \
    --shell /usr/sbin/nologin "$name"
  sudo usermod -aG mojumqaipc "$name"
done
MOJUM_TWO_UID_E2E=1 PYTHONPATH=src \
  sudo -E python -m unittest discover -s tests \
    -p 'test_writer_two_uid_e2e.py' -v
```

**Do not run these commands on a production host.** They are for an
isolated ephemeral test VM only. The default full unittest discovery
skips the root-only e2e class unless `MOJUM_TWO_UID_E2E=1` and the
kernel is Linux with root execution.

## Trust and product scope

- This test demonstrates a real Linux UID isolation policy on a hosted
  CI VM, **not** Android/Pixel 9 compatibility, cross-machine
  authentication or remote access.
- A real allowed client still has permission to invoke supported
  `step` and `acquire` RPC commands. There is no fine-grained user
  consent or semantic AI task authorization.
- A process that controls the **writer's OS UID** can bypass the
  service, and root can bypass normal file permissions. The test
  explicitly demonstrates this residual boundary and fail-closed
  detection at subsequent startup.
- Separate SQLite databases are not crash-atomic. A `SIGKILL` after
  both commits is recoverable via a manual stale-socket cleanup; a
  crash in the Noosphere-committed / authority-uncommitted gap needs
  explicit offline reconciliation and must not auto-adopt a newer head.
- This protocol remains single-host. Copying registry databases to
  two partitioned devices cannot prove an exclusive writer. No
  distributed quorum, authenticated lease or physical motor capability
  is implied.
- No production account setup, deployment, server migration or
  autonomous merge into `main` is performed by this PR.

Before claiming production safety, independently review the whole
stack, including private SQLite WAL sidecars, ACLs, filesystem mount
options, backup/restore rollback, and service sandboxing, then conduct
a controlled host acceptance test.
