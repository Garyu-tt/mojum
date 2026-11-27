# Connectome Coder R01 → 10B CPU research track

R01 is a sparse linear policy trained by terminal rewards on a bounded arithmetic synthesis grammar. It is not a spiking connectome, general coding agent or 10B simulation. It uses external beam search and a fixed grammar; source is compiled and evaluated on unseen inputs. No LLM or target source is used for training. It is independent of Dante and the developmental clock.

## Reproduce
`python experiments/connectome_coder_r01.py --episodes 30000 --seed 42`
Repeat with seeds 43 and 44. Prior numeric results were not backed by execution logs and are withdrawn; run the commands above before reporting any scores.

## Acceptance ladder
1. Reproduce results on CPU and add unit/property tests. Add matched-budget random search and exhaustive oracle; disjoint semantic families, hidden tests, independently generated holdout. Measure training, inference, memory and actual active cells.
2. Replace the linear policy with an explicit sparse recurrent GENESIS-compatible graph: sensory, working memory, goal/decomposition, syntax/AST action, error/reward and episodic memory populations. Local plasticity; compare learned state vs untrained/shuffled weights.
3. Curriculum: expressions, branching, loops, data structures, functions, multi-file programs, then command-line utilities and small services. Program edits, test feedback and repair; no LLM or solution-bearing external planner. Fixed grammar/tooling allowed as actuator, not as the decision maker.
4. Build a bounded sandbox with no network, host secrets or production access; enforce file, resource and execution limits. Independent tests and termination/rollback. Score pass@1, full task success, repair rate and cost.
5. Native CPU-first sparse runtime, procedural deterministic topology with indexed address space, compact state, event scheduling, NUMA awareness, checkpointing of learned edges. Stage gates: 100k, 1M, 10M, 100M, 1B, 10B. Report addressable, instantiated, active, and learned neurons separately; do not count a logical address range as a materialized network.
6. 10B target means neurons (assumption), not parameters. 4 bytes per neuron alone = 40 GB decimal, before edges. 8 explicit outgoing edges per neuron at 8 bytes each = 640 GB decimal. Benchmark memory bandwidth and use sparse active populations, implicit topology and compact plastic state; do not claim 10B runs on 128 GB until measured.
7. Final acceptance: from novel written specification to a complete runnable repository, with tests, documentation, build and iterative repair; independent heldout evaluator. No access to solutions. Test on progressively larger unseen projects. Retain no unsupported capability claims.

R01 source is published; execution and deployment on Mikoshi are unverified.

## GitHub curriculum
Run `python experiments/github_coder_corpus.py Garyu-tt/mojum --out corpus/mojum --limit 100` in an approved environment. Use owned or explicitly licensed repositories; no private repos by default. Student records contain base SHA and title, teacher records contain patches and notes; titles still need leakage review. Merged does not mean tested. Next: checkout exact base in an isolated environment, reconstruct held-out tests, attach CI and review traces, verify independently, split by repository/time and near-duplicate families, then train the neural policy on actual edit actions and test feedback. Do not execute downloaded workflows or expose repository credentials to the learner.
