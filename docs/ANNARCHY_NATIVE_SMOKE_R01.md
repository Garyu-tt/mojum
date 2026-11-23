# ANNarchy native smoke subprocess (R0.1)

Status: opt-in research/CI probe only. This does not activate ANNarchy in
GENESIS or the Aquarium and makes no biological, learning, STDP, or motor
claims.

## Why this slice exists

Draft PR #739 contains a standard-library rate reference and an optional
ANNarchy native runner, but its JIT compilation was explicitly left for a
disposable process with CPU/RAM/time limits. This helper supplies that
process boundary and a pinned exact-head native CI job.

## Invocation

After installing the separately pinned optional engine in an isolated Linux
environment:

    PYTHONPATH=src python tools/annarchy_native_smoke.py

The CLI may also accept `--probe-source <directory>` to select a pinned source
checkout containing `mojum/annarchy_rate_probe.py`. The child runs
`compare_rate_traces` with a fixed two-neuron PUBLIC_SYNTHETIC signal. It never
reads project journals, private observations, real sensors, hardware, or
caller-supplied simulation state. A strict bounded JSON result is written to a
temporary file so compiler logs cannot corrupt it. Nonzero exit, malformed
output, excessive numerical error, timeout, or process-resource failure is a
blocked result.

## Enforced limits and boundaries

On POSIX, the child receives `RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_FSIZE`,
`RLIMIT_NOFILE` and disabled core dumps. The parent enforces a wall-clock
timeout, kills the process group on timeout, uses no shell, closes stdin,
starts with a credential/proxy-stripped allowlisted environment, temporary
HOME/TMP directories, and a bounded result file. Numerical inputs, neuron
count, frames, CPU, memory and wall-time are bounded.

**This is not a complete hostile-code sandbox.** Python's stdlib cannot provide
network namespaces, seccomp, mount isolation, aggregate disk quotas or a
reliable per-child RSS meter portably. `RLIMIT_AS` caps virtual address space,
not measured RSS. The GitHub runner is a disposable, least-privilege job with
no repository secrets and a job timeout; dependency installation needs network
before execution, and the runner does not claim network isolation during the
native process. Do not use this helper with untrusted code or in a privileged,
production, or long-lived service process. For stronger guarantees, run it
inside a separately provisioned container/VM with network disabled and hard
cgroup CPU/memory/PID/disk limits.

## Acceptance boundary

The report proves only that the pinned ANNarchy build returned a trace within
the declared numerical tolerance on this small synthetic fixture in this
runner. It is not a complete cross-platform performance study, does not prove
native artifact reproducibility, and does not establish safe deployment or
compatibility with the real organism. Brian2, STDP, online plasticity,
persistent engine checkpoints, GENESIS integration, licensing review and
independent QA remain separate gates.
