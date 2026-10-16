# Roadmap

This is a plan, not a promise.
Priorities follow what makes the gate trustworthy and useful with real simulators first.

## v0.1.0 (this release)

- Run-result schema 1.0, comparison schema 1.0, JSON Schemas.
- Comparison engine: absolute, relative and combined margins, mean or median, paired and unpaired percentile bootstrap, missing-data and zero-baseline rules, optional Bonferroni correction, conservative policy defaults.
- CLI: `init`, `validate`, `compare`, `report`, `demo`, `adapters`, `schema`.
- Terminal, JSON, JUnit XML and self-contained HTML reports.
- Adapters: `json`, `synthetic`, optional `gymnasium`; entry-point plugin contract with an example CSV plugin.

## v0.2 (next)

1. **LeRobot adapter.** Read `lerobot-eval`'s `eval_info.json` (per-episode reward, success and seed), including multi-task output, with fixtures created from the documented format and validated against real output before release.
2. **Better intervals for binary and rare-event metrics.** Score or exact intervals for differences of proportions (paired and unpaired), selected automatically for 0/1 metrics, with documented coverage from simulation studies included in the test suite.
3. **Coverage study of the bootstrap.** A reproducible notebook-free script that measures actual error rates of the gate across sample sizes and distributions, published in the docs, so users can choose `min_samples` from data rather than intuition.
4. **Baseline management.** A small local store (`physim-gate baseline save/show`) keyed by scenario and branch, still file-based and Git-friendly.
5. **Multi-run suites.** One config that gates several scenarios at once and aggregates verdicts, with per-scenario JUnit suites.
6. **PyPI release** under `physim-gate`, after a final name check, plus a reusable GitHub Action wrapper.
7. **Markdown summary output** for PR comments (`--markdown`), sized for GitHub's comment limits.

## Later

- Adapters for ROS 2 bags (with user-defined metric extraction), PX4 SITL ULog files, and CARLA ScenarioRunner or Leaderboard results. Each only when it can be tested in CI without the full simulator, using recorded fixtures.
- Trajectory- and time-series-level metrics (for example maximum deviation over a rollout) computed by adapters into per-trial scalars, with helpers.
- Equivalence-style gates for "must not improve either" checks (calibration, determinism audits).
- Stratified comparisons by trial labels (weather, terrain, object type), with per-stratum verdicts.
- Optional NumPy acceleration for very large trial counts, kept out of the default install.

## Explicitly out of scope

A physics engine, a scenario DSL, simulator installation, schedulers, Kubernetes, dashboards, hosted services, accounts, LLM-based diagnosis, data lakes, hardware control and safety certification.
PhySim Gate stays a small, local tool that other systems can call.
