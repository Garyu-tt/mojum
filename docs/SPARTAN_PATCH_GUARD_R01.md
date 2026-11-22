# Spartan patch syntax and scope guard R0.1

Issue #478 needs a separate untrusted-model-to-trusted-executor boundary. This
standalone **offline preflight** examines the **complete** Git unified diff
before a trusted executor may consider applying it. It never invokes Git,
runs code, starts workers, reads a worktree or uses credentials.

## Example (run from the repository checkout)

```sh
python3 tools/spartan_patch_guard.py \
  --patch /tmp/model-output.diff \
  --allow-path src/mojum/room_spec.py \
  --allow-path tests/test_room_spec.py
```

The caller, **not the model**, must supply exact authorized repo-relative
paths from the task grant. The model must not be allowed to control the
`--allow-path` arguments. On success, the CLI prints a small JSON report with
SHA-256 of the exact UTF-8 patch bytes, file list and addition/deletion counts;
on failure, it exits with code 2 and prints an error without patch contents.

Fail-closed restrictions: 256 KiB UTF-8 patch, at most 16 files, 128 hunks,
1,200 changed lines; permitted roots `src/mojum/`, `tests/`, `docs/` and
`tools/` only; a path must also match an **exact** task grant. Only same-path
text edits and regular non-executable (`100644`) new files are accepted.
Renames, deletes, binary changes, symlinks, executable mode changes, ambiguous
quoted paths, embedded NUL, CRLF and malformed or truncated hunk bodies are
rejected. These limits are conservative, not a guarantee that code is safe.

## What still must happen before deployment

1. A distinct trusted executor pins the repository and base commit, rejects a
   stale task grant, and checks the file/symlink state of **every** target and
   ancestor in a disposable, isolated worktree (including race protection).
2. The executor runs `git apply --check` and applies only the *same digest*
   checked here. Never pass model-authored flags, paths or shell commands.
3. Untrusted tests run in a network-isolated, credential-free container with
   hard CPU/RAM/time/process/filesystem quotas. No production mounts, Noosphere
   secrets, model provider tokens or GitHub write tokens are available there.
4. A separate constrained publisher submits a draft PR. Independent QA/SHIP
   verifies the exact tested HEAD and BASE and decides acceptance; the coding
   worker cannot approve or merge itself.

This is deliberately one small component of the #478 execution-isolation work
package, **not** a deployed 300-agent farm or a safe sandbox by itself.

## Hermetic verification

```sh
python3 -m unittest discover -s tests -p test_spartan_patch_guard.py -v
```
