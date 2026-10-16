# Roadmap

This is a plan, not a promise.
Priorities follow what makes the gate trustworthy and useful with real simulators first.

## v0.1

v0.1.1 fixed the findings of the [OSS readiness review](oss-readiness-review.md), most importantly score intervals for 0/1 metrics.

- Run-result schema 1.0, comparison schema 1.0, JSON Schemas.
- Comparison engine: absolute, relative and combined margins, mean or median, paired and unpaired percentile bootstrap, missing-data and zero-baseline rules, optional Bonferroni correction, conservative policy defaults.
- CLI: `init`, `validate`, `compare`, `report`, `demo`, `adapters`, `schema`.
- Terminal, JSON, JUnit XML and self-contained HTML reports.
- Adapters: `json`, `synthetic`, optional `gymnasium`; entry-point plugin contract with an example CSV plugin.

## v0.2

- **LeRobot adapter** for `lerobot-eval`'s `eval_info.json`, multi-task and single-task layouts, validated against real PushT output.
- **Sample-size planning** with `physim-gate plan`: exact verdict probabilities for unpaired 0/1 metrics, a normal approximation for continuous ones.
- **Markdown summary** (`--markdown`, `--format markdown`) sized for GitHub's comment limit.
- **GitHub Action** (`uses: kiyeonjeon21/physim-gate@<tag>`) with job summary, outputs and an updating pull-request comment.
- **Release workflow** for PyPI with trusted publishing, ready but not yet enabled.

## Next

1. **PyPI release** under `physim-gate`, once trusted publishing is configured on pypi.org.
2. **Small-sample bootstrap accuracy.** The study shows about 4-5.5% per-side error at 10 trials. Evaluate BCa or studentized intervals, or a higher default `min_samples`, against the same study.
3. **Paired sample-size planning.** Pairing usually needs fewer trials; planning currently assumes unpaired runs.
4. **Baseline management.** A small local store (`physim-gate baseline save/show`) keyed by scenario and branch, still file-based and Git-friendly.
5. **Multi-run suites.** One config that gates several scenarios at once and aggregates verdicts, with per-scenario JUnit suites.
6. **More real LeRobot data.** Multi-task suites such as LIBERO, which need a GPU-capable environment to produce.

## Later

- Adapters for ROS 2 bags (with user-defined metric extraction), PX4 SITL ULog files, and CARLA ScenarioRunner or Leaderboard results. Each only when it can be tested in CI without the full simulator, using recorded fixtures.
- Trajectory- and time-series-level metrics (for example maximum deviation over a rollout) computed by adapters into per-trial scalars, with helpers.
- Equivalence-style gates for "must not improve either" checks (calibration, determinism audits).
- Stratified comparisons by trial labels (weather, terrain, object type), with per-stratum verdicts.
- Optional NumPy acceleration for very large trial counts, kept out of the default install.

## Explicitly out of scope

A physics engine, a scenario DSL, simulator installation, schedulers, Kubernetes, dashboards, hosted services, accounts, LLM-based diagnosis, data lakes, hardware control and safety certification.
PhySim Gate stays a small, local tool that other systems can call.
