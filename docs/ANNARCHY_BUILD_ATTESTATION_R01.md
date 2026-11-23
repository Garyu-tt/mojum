# ANNarchy build attestation R0.1

## Purpose

This is an opt-in, offline **metadata attestor** for the native CPU build proposed
by Mojum issue #691. It records which ANNarchy package/version, compiler,
target, backend, flags, model digest and generated-artifact digest a caller says
were used. Its output is deterministic for the same manifest.

It does not implement an ANNarchy neural population, compile source code, load a
binary, run a neuron model, compare Brian2 traces, or integrate a backend into
GENESIS/Aquarium.

## Wire format

The input is one JSON object with the exact fields below:

| Field | Contract |
|---|---|
| \`schema\` | \`mojum.annarchy-build-manifest/1\` |
| \`package\` | exactly \`ANNarchy\` |
| \`package_version\` | numeric three-component version, optional suffix |
| \`backend\` | \`cpu\` or \`openmp\` |
| \`compiler\` | \`gcc\`, \`clang\`, or \`msvc\` |
| \`compiler_version\` | numeric three-component version, optional suffix |
| \`target\` | \`linux-x86_64\`, \`linux-aarch64\`, \`windows-x86_64\`, or \`macos-arm64\` |
| \`openmp\` | exact JSON boolean; must agree with backend |
| \`threads\` | exact integer 1..64; \`cpu\` means 1, \`openmp\` means 2..64 |
| \`build_flags\` | at most 32 unique allowlisted flags, kept in caller order |
| \`source_revision\` | 40 lowercase hexadecimal characters |
| \`model_sha256\` | 64 lowercase hexadecimal characters |
| \`artifact_sha256\` | 64 lowercase hexadecimal characters |
| \`artifact_size_bytes\` | exact integer 1..512 MiB |

The JSON input limit is 64 KiB. Duplicate object keys, non-finite numbers,
unknown fields, extra nested data, control characters, unsupported flags,
invalid numeric types (including booleans in integer fields), and over-budget
values are rejected.

Allowed flag metadata: \`-O0\`..\`-O3\`, \`-std=c++17\`, \`-fopenmp\`, \`-DNDEBUG\`,
bounded \`-march=\` / \`-mtune=\` values, \`/O1\`, \`/O2\`, and \`/openmp\`. This is a
conservative metadata profile, not a complete compiler command-line language.
The manifest intentionally excludes arbitrary environment dumps, hostnames,
paths, credentials, compiler commands, and free-form notes.

## Run

From the repository root:

\`\`\`sh
python tools/annarchy_build_attestation.py < build-manifest.json
\`\`\`

The command reads at most 64 KiB from standard input and writes a JSON receipt
to standard output. It does not open a supplied filename or inspect an artifact
path. Exit status is 0 for a valid receipt and 2 for a rejected manifest.

Focused hermetic tests:

\`\`\`sh
python -m unittest tests.test_annarchy_build_attestation -v
\`\`\`

## Receipt semantics and limitations

\`manifest_sha256\` is SHA-256 over canonical ASCII JSON (sorted object keys,
compact separators, preserved build-flag order). The artifact and model
hashes are **caller supplied** metadata. This utility does not read either
object and therefore cannot prove those digests match actual bytes.

A passing receipt does not authenticate its author, protect against a
malicious compiler, prove a binary is safe, prove deterministic/reproducible
compilation, establish ABI compatibility, show OpenMP actually ran, measure
CPU/RAM/compile cost, or validate neural equations/STDP. No runtime code should
load an artifact merely because this receipt exists. Native compiler/ANNarchy
smoke tests and independent artifact provenance remain separate acceptance
gates.

No optional dependencies are imported; no ANNarchy, Brian2, NumPy, compiler,
network, filesystem path, PRIVATE Noosphere record, hardware, or motor-control
surface is accessed by this module.
