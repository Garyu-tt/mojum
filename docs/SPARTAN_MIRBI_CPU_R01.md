# Spartan: opt-in mirbi-linux CPU inference fleet R0.1

This is a **specific, locally verified** operator profile of the existing
`tools.spartan_local_fleet` implementation, **stacked on PR #533**. It does
not reassign GPU hardware or make a networked shared SQLite coordinator.

## Live inventory verified 2026-11-22

| Local port | Model process | `/slots` | Context per slot |
| --- | --- | ---: | ---: |
| 48081 | Existing Qwen2.5-Coder-3B Q4 | 2 | Check live |
| 48082 | New Qwen3-1.7B Q4, no-think | 6 | 2048 |
| 48083 | New second Qwen2.5-Coder-3B Q4 | 4 | 2048 |
| 48084 | New third Qwen2.5-Coder-3B Q4 | 4 | 2048 |

Each of the three new services runs as non-root `mirbi` with a loopback-only
listener, private service umask and bounded systemd CPU/RAM. They restart on
failure, persist after reboot and do not consume GPU VRAM.

**On-host receipt:** `16` advertised slots; new ports `48082`, `48083`,
`48084` passed `14/14` simultaneous short `/v1/chat/completions` requests,
**3.54 s** warmed wall clock across the three servers, with available RAM
about **9.5 GiB** afterward. This is short-request admission evidence, not
a full-length coding throughput benchmark. Qwen3 previously returned an
empty final answer on a 128-token budget because its thinking trace used the
tokens; the new service was restarted with `enable_thinking=false` and a
128-token nonempty final answer was verified. No credentials or remote
ports were exposed.

The local profile is **opt-in**, not the fleet default. The original
`vm200` profile remains unchanged. All requests use exactly pinned
`127.0.0.1` ports, no external proxy or redirect. This is not an open
port/config-file injection feature.

## Example: isolated review-only batch on mirbi-linux

Run from a checked-out branch containing this profile. Use a separate,
**owner-private** coordinator database from the VM200 one (SQLite WAL must
not be shared over the network). Assign *different, independently testable*
issue scopes across hosts; do not run the same issue twice.

~~~sh
install -d -m 700 "$HOME/.local/state/mojum-spartan" \
  "$HOME/.local/state/mojum-spartan/proposals"
# Resolve the HOME symlink: the coordinator forbids symlinked SQLite parents.
STATE="$(realpath -e "$HOME/.local/state/mojum-spartan")"
python3 tools/spartan_coordinator.py \
  --db "$STATE/tasks.sqlite3" init --slots 300
# Enqueue reviewed, real Mojum tasks (no secrets) from a 0600 JSON file:
python3 tools/spartan_coordinator.py \
  --db "$STATE/tasks.sqlite3" enqueue --json ./tasks.json
python3 -m tools.spartan_local_fleet \
  --db "$STATE/tasks.sqlite3" \
  --output-dir "$STATE/proposals" \
  --backend-profile mirbi-linux --status

python3 -m tools.spartan_local_fleet \
  --db "$STATE/tasks.sqlite3" \
  --output-dir "$STATE/proposals" \
  --backend-profile mirbi-linux --agents 300 --max-jobs 6 \
  --max-cpu-inflight 2
~~~

The run measures actual overlapping `call_model()` calls and writes private
0600 proposals. The default admission remains **one active request per CPU
process** until a staged load test demonstrates useful throughput; the explicit
`--max-cpu-inflight 2` shown above permits at most 8 concurrent model
calls across the four CPU processes, even though 16 slots are advertised.
The upper bound remains 4 per CPU process, and `--max-jobs` must be <= 32.

These agents **do not run generated code or tests, publish PRs or merge**.
Send accepted proposals to a separate credential-free sandbox/executor,
then independently verified exact-head CI and review. The source PR remains
Draft until that verification. Monitor CPU, systemd memory, queue state,
model response content, proposal quality and accepted PRs/hour. The
`mirbi-linux` and `vm200` inventories cannot be naively added as proven
*integrated* fleet concurrency: remote scheduling/authentication remains
a separate task under #478.

## Operator rollback of the three new model services

~~~sh
sudo systemctl disable --now mojum-qwen3-17b-r01.service \
  mojum-qwen25-coder3b-r01.service mojum-qwen25-coder3b-r02.service
~~~

Do not stop the pre-existing `48081` model. Removing this opt-in code profile
also leaves all host services untouched.
