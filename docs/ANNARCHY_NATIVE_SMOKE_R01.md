# ANNarchy native smoke subprocess (R0.1)

Status: opt-in research/CI probe only. This does not activate ANNarchy in
GENESIS or the Aquarium and makes no biological, learning, STDP, or motor
claims.

## Why this slice exists

The ANNarchy rate-shadow comparison in PR #739 has a standard-library
reference and optional native runner, but JIT compilation was documented as
requiring a disposable process with external CPU/RAM/time limits. This helper
makes those limits explicit and adds one exact-PR-head native check.

## Invocation

After installing the separately pinned optional engine in an isolated Linux
environment:

    PYTHONPATH=src python tools/annarchy_native_smoke.py --frames 32

The tool invokes the existing `mojum.annarchy_rate_probe.compare_rate_traces`
with a fixed two-neuron PUBLIC_SYNTHETIC signal. It never reads project journals,
private observations, real sensors, hardware, or user-supplied simulation
state. Stdout is parsed as a strict, bounded JSON receipt; compiler stderr is
not echoed because it can include local paths. Nonzero exit, malformed output,
excessive error, timeout, or process-resource failure yields a blocked result.

## Enforced limits and boundaries

On POSIX, the child receives `RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_FSIZE`,
`RLIMIT_NOFILE` and disabled core dumps. The parent enforces a wall-clock
timeout, kills the child process group on timeout, bounds stdout/stderr files,
uses no shell, closes stdin, and starts with a credential/proxy-stripped
allowlisted environment and temporary HOME/TMP directories. Numerical inputs,
neuron count, frames, CPU, memory and wall-time are bounded.

**This is not a complete hostile-code sandbox.** Python's stdlib cannot provide
network namespaces, seccomp, mount isolation, aggregate disk quotas or a
reliable per-child RSS meter portably. `RLIMIT_AS` caps virtual address space,
not measured RSS. The GitHub runner is a disposable, least-privilege job with
no repository secrets and a job timeout; package installation needs network
before execution, and the runner does not claim network isolation during the
native process. Do not use this helper with untrusted code or in a privileged,
production, or long-lived service process. For stronger guarantees, run it
inside a separately provisioned container/VM with network disabled and hard
cgroup CPU/memory/PID/disk limits.

## Acceptance boundary

The report proves only that this pinned ANNarchy build returned a trace within
the declared numerical tolerance on this small synthetic fixture in this
runner. It is not a complete cross-platform performance study, does not prove
native artifact reproducibility, and does not establish safe deployment or
compatibility with the real organism. Brian2, STDP, online plasticity,
persistent engine checkpoints, GENESIS integration, licensing review and
independent QA remain separate gates.
