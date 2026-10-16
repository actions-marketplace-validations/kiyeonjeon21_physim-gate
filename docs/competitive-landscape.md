# Competitive landscape

This page compares PhySim Gate with related open-source projects and tools.
It was first written in October 2026 from public READMEs, then revised after reading the source of RoboCI and robot-sim-test-harness (shallow clones, read only; no code was run or reused).
File references point into those repositories at the time of reading.
Items marked *unverified* could not be confirmed.
Corrections are welcome.

## RoboCI

[github.com/gagandeepreehal/roboci](https://github.com/gagandeepreehal/roboci), public alpha (v0.0.3 at the time of writing).

- Local-first evaluation of robotics and embodied-AI code before hardware: closed-loop scenario suites in a simulator, plus open-loop evaluation on datasets.
- File-based runs (`summary.json`, `metrics.json`, `failures.json`, `provenance.json`, `regression.json`, replay timelines) and named baselines.
- Failures have a lifecycle (open, fixed, regressed). Scenarios carry assertions such as no-collision and maximum speed.
- `roboci compare` checks a run against a baseline with `--max-regression-percent` (default 5). A metric regresses if it is in a hardcoded direction list, moved the wrong way, and changed by more than the threshold (`roboci/regression/comparator.py`). Metrics outside the lists are "neutral" and never gate. Each side is one aggregate per run. There is no statistical testing in the code.
- Zero baselines have an explicit rule (an absolute fallback). Metrics missing on either side are skipped silently.
- A Typer CLI plus a FastAPI and Next.js dashboard. Mock simulator by default, with CARLA, Isaac Sim, Gazebo and ROS 2 adapters that need those tools installed.
- JSON, HTML and Markdown reports. CI integration through exit codes and a PR-comment workflow.

**Overlap:** high. Both compare runs against baselines, record provenance and integrate with CI.
**Difference:** RoboCI orchestrates simulation and ships a dashboard. PhySim Gate only evaluates existing results and focuses on the decision itself: per-trial distributions, uncertainty intervals, explicit inconclusive outcomes, user-declared directions, and missing data that invalidates a metric instead of being skipped.
Zero-baseline handling is not a differentiator; RoboCI has it too.
RoboCI's provenance capture (git state and hashes of agent, suite and dataset) is comparable to or richer than PhySim Gate's.
The two could be combined: RoboCI results could be converted into PhySim Gate run results by an adapter.

## robot-sim-test-harness

[github.com/Pratyush150/robot-sim-test-harness](https://github.com/Pratyush150/robot-sim-test-harness), an early-stage project.

- Regression tests for robot behavior written as YAML scenarios with scalar and temporal assertions (always, eventually, until), on a built-in kinematic mock.
- Deterministic stepping, seeded random streams, trace recording, and a `diff` command that finds where two traces diverge.
- Compares against a committed baseline with per-metric tolerances. Pass-to-fail transitions fail the build; metric drift warns unless strict mode is on.
- Grid and Monte Carlo sweeps reported as pass-rate tables, and bisection to find failure boundaries. `--min-pass-rate` gates on the raw rate. There is no statistical testing in the code.
- Metric drift is checked two-sided against absolute and relative tolerances (`src/simharness/ci.py`), so improvements are flagged too. Zero baselines disable the relative band. Metrics without a tolerance are skipped silently.
- Terminal, JUnit XML, JSON and self-contained HTML outputs. Gazebo, AirSim and Isaac adapters exist but are described as untested.

**Overlap:** baseline comparison, tolerances, JUnit and HTML output.
**Difference:** robot-sim-test-harness is a scenario runner with assertions on traces. PhySim Gate compares the distributions of per-trial outcomes from any runner, with paired bootstrap intervals rather than fixed tolerances on single values.

## LeRobot (`lerobot-eval`)

[github.com/huggingface/lerobot](https://github.com/huggingface/lerobot)

- Evaluates a policy in simulation (for example LIBERO and MetaWorld, or environments shared on the Hugging Face Hub) or on real hardware.
- Writes `eval_info.json` with per-episode values and aggregates (`avg_sum_reward`, `pc_success`, `n_episodes`, ...), including a 95% Wilson interval on the success rate (`lerobot/utils/eval_stats.py`). Since 0.4.0 it stores per-task lists (`per_task` with `sum_rewards`, `max_rewards`, `successes`); 0.3.x stored `per_episode` records that also included each episode's `seed` (checked in the `v0.3.3` and `v0.4.0` tags and at commit `146d847`).
- No baseline-versus-candidate comparison in the evaluation script.

**Overlap:** none in the comparison step.
**Relationship:** LeRobot is a natural producer of results for PhySim Gate, and the built-in `lerobot` adapter reads both `eval_info.json` layouts (see [adapters](adapters.md#lerobot)).

## Scenic

[github.com/Scenic-Foundation/Scenic](https://github.com/Scenic-Foundation/Scenic)

- A probabilistic programming language and compiler for scenarios of cyber-physical systems, with interfaces to simulators such as CARLA, MetaDrive, Webots, X-Plane and Isaac (experimental).
- No baseline comparison or regression statistics are documented.

**Relationship:** complementary and upstream. Scenic decides *which* situations to simulate; PhySim Gate decides whether a code change made outcomes worse across them. Scenario DSLs are out of scope for PhySim Gate.

## VerifAI

[github.com/BerkeleyLearnVerify/VerifAI](https://github.com/BerkeleyLearnVerify/VerifAI)

- Formal analysis of systems with ML components: temporal-logic falsification, fuzz testing, parameter synthesis, counterexample analysis and data augmentation, often driven by Scenic programs.
- Examples for CARLA, Webots, X-Plane and Gym.
- No CI gating or baseline comparison found. Output formats and statistics were *unverified*.

**Relationship:** complementary. Falsification searches for failures of one system; regression gating compares two versions of a system on the same distribution of conditions.

## ROS 2 tracing

[github.com/ros2/ros2_tracing](https://github.com/ros2/ros2_tracing)

- LTTng-based tracepoints in core ROS 2 packages (Linux only), a `ros2 trace` command and launch integration. Analysis lives in separate projects.
- No comparison or statistics.

**Relationship:** a possible artifact source. A future adapter could reduce traces to per-trial metrics such as callback latency percentiles.

## Summary

| | Runs simulators | Baseline comparison | Uncertainty-aware | Explicit inconclusive outcome | Simulator-independent input | Plugin contract | Infrastructure needed |
|---|---|---|---|---|---|---|---|
| PhySim Gate | no | yes | yes (bootstrap or score interval of the difference) | yes | yes | entry points | none |
| RoboCI | yes | yes (percentage threshold on one aggregate per run) | no (verified in code) | no | through its adapters | hardcoded registry | optional dashboard |
| robot-sim-test-harness | yes (mock) | yes (absolute and relative tolerances) | no (verified in code) | no | through its adapters | `register()` registry | none |
| LeRobot eval | yes | no | Wilson CI on success | n/a | n/a | n/a | none |
| Scenic / VerifAI | through interfaces | no | n/a | n/a | n/a | simulator interfaces | none |
| ros2_tracing | no | no | n/a | n/a | n/a | n/a | LTTng (Linux) |

## Existing Python tools

A robotics developer may already have tools that cover parts of this job.
PhySim Gate does not replace them, and its statistics are not novel.

| Tool | What it already does | What it does not do for this use case |
|---|---|---|
| `scipy.stats.bootstrap`, `permutation_test` | Paired and unpaired bootstrap (percentile, basic, BCa) and permutation tests | No run schema, margins, verdicts or CI outputs |
| statsmodels | Non-inferiority tests (`ttost_paired`, `ttost_ind`, `tost_proportions_2indep`) and Newcombe or Wilson intervals | Same: a library of tests, not a gate |
| rliable | Stratified bootstrap intervals, IQM and probability of improvement for multi-task, multi-seed RL evaluation | No pass/fail gate, I/O schema or missing-data rules |
| asv, Bencher | Statistical regression detection for benchmarks (asv: Mann-Whitney U; Bencher: several threshold tests) with history | Built around timing benchmarks and their own harness or server |
| pytest-regressions, pytest-benchmark, MLflow `MetricThreshold` | Point-value tolerances against stored baselines | No uncertainty |
| Apache Otava (formerly Hunter), Evidently | Change-point detection over history; data drift tests | Different question (history or input drift, not a two-run non-inferiority decision) |

PhySim Gate's statistics could be replaced by scipy or statsmodels calls with little change in behavior.
The part that appears to be missing in the robotics ecosystem is narrower: a simulator-independent per-trial result schema with pairing keys, explicit rules for failed trials and missing values, a three-way verdict (regression, non-inferior, inconclusive) and CI-ready outputs in one small tool.
Whether that gap is worth a separate project depends on adoption; it has not been tested with real users yet.

## Name check

At the time of writing, `physim-gate` was not registered on PyPI (the JSON API returned 404 for `physim-gate` and `physim_gate`), and a GitHub repository search for `physim-gate` returned no results.
A different, unrelated PyPI project named `physim` exists (a library for physics animation videos).
The names are distinct, but users searching for "physim" may see both.
PhySim Gate has been published on PyPI as `physim-gate` since v0.2.0.
