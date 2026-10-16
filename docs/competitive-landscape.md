# Competitive landscape

This page compares PhySim Gate with related open-source projects.
It was written in October 2026 from each project's public README and documentation.
The implementations were not inspected or reused.
Items marked *unverified* could not be confirmed from the public material that was read.
Corrections are welcome.

## RoboCI

[github.com/gagandeepreehal/roboci](https://github.com/gagandeepreehal/roboci), public alpha (v0.0.3 at the time of writing).

- Local-first evaluation of robotics and embodied-AI code before hardware: closed-loop scenario suites in a simulator, plus open-loop evaluation on datasets.
- File-based runs (`summary.json`, `metrics.json`, `failures.json`, `provenance.json`, `regression.json`, replay timelines) and named baselines.
- Failures have a lifecycle (open, fixed, regressed). Scenarios carry assertions such as no-collision and maximum speed.
- `roboci compare` checks a run against a baseline with `--max-regression-percent`. No statistical testing is described (*unverified* whether the code has any).
- A Typer CLI plus a FastAPI and Next.js dashboard. Mock simulator by default, with CARLA, Isaac Sim, Gazebo and ROS 2 adapters that need those tools installed.
- JSON, HTML and Markdown reports. CI integration through exit codes and a PR-comment workflow.

**Overlap:** high. Both compare runs against baselines, record provenance and integrate with CI.
**Difference:** RoboCI orchestrates simulation and ships a dashboard. PhySim Gate only evaluates existing results and focuses on the decision itself: uncertainty-aware evidence, explicit inconclusive outcomes, and documented semantics for missing data and zero baselines.
The two could be combined: RoboCI results could be converted into PhySim Gate run results by an adapter.

## robot-sim-test-harness

[github.com/Pratyush150/robot-sim-test-harness](https://github.com/Pratyush150/robot-sim-test-harness), an early-stage project.

- Regression tests for robot behavior written as YAML scenarios with scalar and temporal assertions (always, eventually, until), on a built-in kinematic mock.
- Deterministic stepping, seeded random streams, trace recording, and a `diff` command that finds where two traces diverge.
- Compares against a committed baseline with per-metric tolerances. Pass-to-fail transitions fail the build; metric drift warns unless strict mode is on.
- Grid and Monte Carlo sweeps reported as pass-rate tables, and bisection to find failure boundaries. No significance testing is described.
- Terminal, JUnit XML, JSON and self-contained HTML outputs. Gazebo, AirSim and Isaac adapters exist but are described as untested.

**Overlap:** baseline comparison, tolerances, JUnit and HTML output.
**Difference:** robot-sim-test-harness is a scenario runner with assertions on traces. PhySim Gate compares the distributions of per-trial outcomes from any runner, with paired bootstrap intervals rather than fixed tolerances on single values.

## LeRobot (`lerobot-eval`)

[github.com/huggingface/lerobot](https://github.com/huggingface/lerobot)

- Evaluates a policy in simulation (for example LIBERO and MetaWorld, or environments shared on the Hugging Face Hub) or on real hardware.
- Writes `eval_info.json` with per-episode records (`episode_ix`, `sum_reward`, `max_reward`, `success`, `seed`) and aggregates (`avg_sum_reward`, `pc_success`, `n_episodes`, ...). The logs mention a 95% Wilson interval on the success rate (the computation itself was *unverified*).
- No baseline-versus-candidate comparison in the evaluation script.

**Overlap:** none in the comparison step.
**Relationship:** LeRobot is a natural producer of results for PhySim Gate. A LeRobot adapter is the first item on the [roadmap](roadmap.md).

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
| PhySim Gate | no | yes | yes (bootstrap of the difference) | yes | yes | entry points | none |
| RoboCI | yes | yes (percentage threshold) | not described | not described | through its adapters | adapters | optional dashboard |
| robot-sim-test-harness | yes (mock) | yes (tolerances) | not described | not described | through its adapters | simulator base class | none |
| LeRobot eval | yes | no | Wilson CI on success | n/a | n/a | n/a | none |
| Scenic / VerifAI | through interfaces | no | n/a | n/a | n/a | simulator interfaces | none |
| ros2_tracing | no | no | n/a | n/a | n/a | n/a | LTTng (Linux) |

"Not described" means the public documentation did not mention it, not that it is absent.

## Name check

At the time of writing, `physim-gate` was not registered on PyPI (the JSON API returned 404 for `physim-gate` and `physim_gate`), and a GitHub repository search for `physim-gate` returned no results.
A different, unrelated PyPI project named `physim` exists (a library for physics animation videos).
The names are distinct, but users searching for "physim" may see both.
Nothing has been published under the `physim-gate` name yet.
