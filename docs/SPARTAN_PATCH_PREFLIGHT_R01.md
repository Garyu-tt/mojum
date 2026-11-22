# Spartan patch preflight R0.1 (issue #478)

A deterministic, credential-free **read-only lexical gate** for untrusted worker patch proposals. This is a small independent component, not an executor or a replacement for security isolation.

## Usage

Agent workers produce a patch as data. The write-scoped executor must stage a disposable worktree based on a pinned commit, then run:

~~~sh
python3 tools/spartan_patch_preflight.py \
  --allow src/mojum/ --allow tests/ \
  --checkout "$ISOLATED_WORKTREE" < proposed.patch
~~~

The command returns JSON with status=candidate and SHA-256 on accepted input (exit 0), or status=reject and a reason on rejected input (exit 2). The allowlist is mandatory, repeated per directory, and matched literally against repository-relative paths; there are no default writable paths.

A candidate is **not** an approval to execute or merge. An authorized executor must independently verify the requested task's path scope, SHA-256 of the exact proposed patch, immutable base commit, and isolated checkout. It must apply with Git's own patch verification (without --unsafe-paths), inspect resulting tree/modes, run tests in a separate credential-free, network-restricted sandbox, and publish a Draft PR through a distinct write-scoped identity. Avoid passing GitHub or provider secrets into any worker process or test runner.

## Restricted supported subset

- Unified Git diffs with unquoted diff --git a/path b/path, index metadata, and balanced @@ hunks.
- Modifications to regular files and creation of mode 100644 regular files only.
- ASCII pathname segments without dot-prefixed components, traversal, backslashes, whitespace or quoted-path syntax; no path deletion, move, copy, submodule, executable/symlink mode changes, binary patch, combined diff or EOF marker.
- Explicit directory prefixes required and exactly matched. Note that .github is intentionally unavailable because dot-prefixed paths are rejected even when listed.
- Maximum raw input 256 KiB, 16 changed files, 32 hunks per file and 400 added/deleted lines; duplicate changed paths and malformed hunks reject.
- Optional checkout preflight verifies parent directories and existing target file types via lstat, so symlinks are refused at the time of inspection.

This subset intentionally rejects some legitimate Git patches. Those require a separately reviewed, more privileged change path, never an automated bypass.

## Limitations and attack surface

This tool does **not** authenticate the patch's source, validate code semantics, inspect secrets inside lines, fully emulate git apply, defend against a race between lstat and application, secure an adversarial filesystem, or enforce runtime CPU, RAM, time, network and process budgets. It performs no filesystem writes and never invokes shell commands. A positive result must never be interpreted as permission to apply on a live node or as independent acceptance under #415.

Future integrations must use a pinned tree and task-level allowed paths, run patch preflight before any write, then gate exact-head/target-base CI and independent review. The existing 300-slot coordinator (#477) and provider work are distinct and do not become live workers from this change.

## Verification

~~~sh
python3 -m unittest discover -s tests -p test_spartan_patch_preflight.py -v
python3 -m compileall -q tools/spartan_patch_preflight.py tests/test_spartan_patch_preflight.py
~~~

The 14 focused hermetic tests cover valid edits/additions, scope, traversal, mode changes, binary and malformed data, hunk budgets, symlink checkout and CLI fail-closed exit status. CI and independent acceptance must validate the **final PR HEAD** on all mandatory supported Python versions.
