# Mojum R0.4 — private Linux/Unix writer service

An **opt-in, CPU-first, single-host writer service** over an AF_UNIX socket.
Its single owner process creates one `GuardedAquarium`, and all supported
runtime mutations go through bounded JSON RPC. This builds on PR #71
(local authority-guarded journal writes), which depends on the earlier
portable checkpoint and lineage PR stack.

The service has **no TCP, HTTP, WebSocket or remote listener**. It uses
Linux `SO_PEERCRED` to reject peers with UIDs other than the service owner,
puts its socket in an already existing owner-only `0700` directory, and
sets the socket mode to `0600`. This authenticates a **local OS UID**,
not a specific application, user device or node certificate.

## Startup

First build a real synthetic Aquarium journal, take a cold archive, and
explicitly provision `LocalAuthority` for a canonical writer named
`node-a` as documented in `docs/LOCAL_AUTHORITY_HANDOFF_R01.md`.
Neither the writer service nor its guarded Aquarium will create missing
journals or registries on startup.

Prepare two private directories owned by the service process:

```bash
mkdir -m 700 ./ipc ./private-checkpoints

PYTHONPATH=src python -m mojum.writer_service serve \
  --socket "$(pwd)/ipc/writer.sock" \
  --db "$(pwd)/dante-source.sqlite3" \
  --registry "$(pwd)/local-authority.sqlite3" \
  --archive-dir "$(pwd)/private-checkpoints" \
  --genome ./examples/genesis_seed.json \
  --graph ./examples/first_loop.json \
  --writer node-a --epoch 1 --generation 1
```

The server does not print the private event journal. It serves **one
request at a time** from accepted same-UID local clients. A client sends
one length-prefixed (network-order 32-bit length, max 64,000 bytes)
UTF-8 JSON object and receives one length-prefixed JSON object.

Client usage via stdin (no private acquisition event in process argv):

```bash
echo '{"op":"status"}' | PYTHONPATH=src python -m mojum.writer_service \
  request --socket "$(pwd)/ipc/writer.sock"

echo '{"op":"step","expected_generation":1,"dx":0.01,"dy":0}' \
  | PYTHONPATH=src python -m mojum.writer_service request \
    --socket "$(pwd)/ipc/writer.sock"
```

The exact JSON operations supported are:

| Operation | Required input fields | State effect |
| --- | --- | --- |
| status | op | Read only (checks current registry head and writer) |
| step | op, expected_generation, dx, dy | One verified Noosphere frame, tick +1, generation +1 |
| acquire | op, expected_generation, event | One verified acquired-gene event, tick unchanged, generation +1 |
| export | op, expected_generation, filename | New private cold archive in preconfigured directory; no state generation change |

The `expected_generation` is mandatory for mutating/export operations so
stale clients cannot unknowingly mutate a newer state. Export names must
be a safe single path segment, not an absolute or relative path outside
the private archive directory. Existing archive files are never replaced.

Only status/checkpoint metadata, journal hashes, tick, writer epoch and
generation or archive manifest hash are returned. The full PRIVATE memory
and raw event payloads are **not returned through the socket**.

## Host and crash model

- This is a **cooperative single-owner process**. Linux filesystem
  permissions must additionally prevent untrusted client processes from
  opening the underlying journal or authority registry for direct writing.
  If server and clients run as the same Unix user, **same-UID clients can
  still bypass the service via direct SQLite access**; UID-based socket
  checks are not a sufficient security boundary by themselves.
- The checked local authority registry is not a distributed consensus
  store. Two isolated copies of it can each permit a writer. Do not deploy
  this across separate devices as a split-brain solution.
- SQLite writer reservation keeps other *cooperating* guarded clients out
  during a local mutation. Cross-file commits (Noosphere journal vs authority
  registry) are **not atomic**. A crash in the gap leaves a mismatching head:
  the guard refuses automatic reopening until explicit reconciliation.
- If the registry is frozen, unavailable, replaced, or its generation
  differs, the service rejects new writes. No automatic promotion, timeout
  takeover or safety reset is implemented.
- Malformed requests, invalid movement ranges and rejected acquisition
  events are checked before a committed Noosphere mutation. If a rejected
  mutation leaves the journal head/count/tick unchanged, the guarded writer
  can continue accepting later valid requests; **only** a possible
  post-commit/pre-anchor failure poisons it. Unbounded floating-point
  exponents such as JSON 1e999 are rejected at the RPC parser.
- An existing socket path is never removed at startup; on graceful shutdown
  the process removes only its own socket inode. No remote fallback.
- The service's exported checkpoints still contain plaintext PRIVATE events
  and learned synthetic state; protect the private archive directory and
  transfer archives only over a suitable trusted channel.
- An acquisition request remains a trusted local write input: its event
  payload is checked by the Aquarium/GENESIS validators, not by a general
  authorization engine.
- Linux/Unix process tests do not prove Android Pixel 9 background-service
  compatibility, secure app sandboxing, physical device execution, MuJoCo,
  real consciousness, or any remote/mesh failover semantics.

## Test evidence

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_writer_service_r01.py' -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Tests start real separate Python writer service processes and use local
Unix sockets for step/acquire, stale generation refusal, registry freeze,
private export, malformed and oversized frames, duplicate JSON keys,
unknown operations, duplicate service instances, wrong permissions,
registry loss and cross-process CLI. No secrets or private user journals
are checked into the repository.

**Next:** put the service and both SQLite databases under a dedicated
unprivileged OS account so client app UIDs cannot open the journal. Grant
specific client identities access to a group-owned socket or per-peer
authenticated channel and test that read-only clients cannot bypass it.
Then develop a partition-safe distributed authority protocol.
