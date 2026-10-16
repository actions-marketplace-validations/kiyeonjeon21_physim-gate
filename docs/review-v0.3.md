# Review of v0.3.0: comparison integrity, statistics, release and integration

This review looks at PhySim Gate v0.3.0 from the point of view of a robotics infrastructure engineer and a statistical software reviewer.
It was done on 2026-10-17 against tag `v0.3.0` (commit `9eb94d8`).
Line numbers refer to that tag unless a finding says otherwise.

Every finding is one of three kinds:

- **Confirmed defect:** reproduced end to end, with behavior that contradicts the documentation or the intent.
- **Design risk:** works as designed and documented, but the design invites a wrong conclusion.
- **Unverified hypothesis:** plausible from reading the code, not reproduced.

Priorities:

- **P0:** a silently wrong verdict or exit code in realistic use.
- **P1:** wrong or misleading in an important but visible way, or wrong in edge cases.
- **P2:** hygiene and design.

Only P0 defects were fixed in this pass, each with a test that fails on `v0.3.0`.
Everything else is listed for later.

## Summary

- **Three P0 defects were found and fixed.**
  - A candidate could leave out trials and pass.
  - A repeated key in a gate config, run result or suite file silently replaced the first entry.
  - An adapter calling `sys.exit(0)` made `compare` exit 0 without comparing anything.
- **The statistics are correct where it matters.**
  - Wilson, unpaired and paired Newcombe intervals, direction handling, Bonferroni and the exact planner all agree with independent implementations to machine precision.
  - The Monte Carlo error-rate table was reproduced within its uncertainty.
  - Two P1 statistical defects remain:
    - The expanded bootstrap uses too many degrees of freedom when sample sizes are very unequal.
    - Planning from an underpowered pilot falls back to the wrong method.
- **Versions are consistent** across `pyproject.toml`, `__version__`, `CHANGELOG.md`, the git tag, the GitHub release, PyPI and every `@v0.3.0` reference.
  The PyPI and GitHub release files are byte-identical, and the sdist's own tests pass.
- **The GitHub Action has no shell injection**, but it has two P1 defects in failure handling (stale verdict, fork pull requests).
- **For real-world integration, PX4 ULog is the smaller first step than ROS 2.**
  It was prototyped against a real SITL log, as described at the end.

## How the review was done

| Area | What was done |
|---|---|
| Integrity | Hand-written run results and configs exercised through the real CLI: dropped trials, duplicate keys, status, scenario and simulator changes, seeds, store references, suites |
| Statistics | Independent reimplementation or comparison with scipy and statsmodels; all 2,400 2x2 tables with cells 0..6 for the paired interval; brute-force planner checks over 3,450 cases; Monte Carlo spot checks at 400 repetitions |
| Release | Version strings, tag, release, PyPI JSON, downloaded wheel and sdist (contents, metadata, sdist tests on Python 3.11), CI and release workflows, `action.yml` run locally with simulated inputs |
| Security | YAML loading, HTML and Markdown escaping with hostile strings, store and suite names, plugin discovery and conflicts, exit-code paths |
| Tests | Full suite and coverage before and after the fixes |

## P0: fixed

| # | Kind | Finding | Fix and test |
|---|---|---|---|
| P0-1 | Confirmed defect | **Omitted trials did not count as missing.** `engine.py:368` counted missing values only among the trials each run reported. A candidate with 20 of the baseline's 30 trial ids, the other 10 simply left out, got `PASS` and exit 0. Reporting the same 10 trials with `null` values gave `INVALID` and exit 3. With `pairing: paired` the unmatched trials were dropped with a note, also `PASS`. Leaving out crashed trials therefore beat reporting them, which is exactly what the strict `max_missing_fraction` default is meant to prevent (`docs/comparison-semantics.md:43-45`). | When the runs share trial ids, an id reported by only one run counts as a missing value of the other run, and as a failure in `physim.failure_rate`, so the documented safeguard (raise `max_missing_fraction`, gate the failure rate) still catches omissions. Runs with disjoint ids are still independent samples. `tests/test_integrity.py` (6 tests); two engine tests that encoded the old behavior were updated. |
| P0-2 | Confirmed defect | **Repeated keys were silently accepted.** `config.py:163` and `suite.py:117` used `yaml.safe_load`, and `schema.py:286` and `adapters/lerobot.py:124` used `json.loads`. All of them keep the last value of a repeated key. A second `err:` entry in `gate.yaml` turned a `FAIL` (exit 1) into a `PASS` (exit 0). A suite with the same scenario name twice ran one scenario and dropped the regressing one. | A YAML loader and a JSON hook that reject repeated keys, used for configs, suites, run results, comparison results, LeRobot output and the synthetic and gymnasium specs. YAML merge keys keep their override semantics. `tests/test_integrity.py` (4 tests). |
| P0-3 | Confirmed defect | **An adapter could set the exit code.** `cli.py:127` re-raises `SystemExit`, and `cli.py:218` called `adapter.load` directly. An adapter, or a gymnasium policy, that called `sys.exit(0)` ended `compare` with exit 0, no comparison written, and a passing GitHub Action step. | All adapter calls go through `adapters.base.load_with_adapter`, which turns `SystemExit` into an `AdapterError` (exit 2). `tests/test_integrity.py::test_adapter_calling_sys_exit_is_an_error_not_a_pass`. |

## P1

### Confirmed defects

| # | Finding | Where | Suggested fix |
|---|---|---|---|
| P1-1 | **The expanded bootstrap undercovers with very unequal sample sizes.** It uses `n = min(nb, nc)` but `df = nb + nc - 2`. With 10 vs 1000 normal trials at the margin, the per-side error was 4.3% and 4.7% (4,000 repetitions), against 2.5% nominal. With 10 vs 10 it was 2.9%. A large stored baseline against a small candidate is a realistic case. | `stats.py:128` | Welch-Satterthwaite degrees of freedom from the sample variances, or simply `df = min(nb, nc) - 1`. Test: `_expanded_alpha` for 10 vs 1000 at most about 0.02, plus a seeded study row. |
| P1-2 | **`plan --from` on an underpowered pilot changes method and confidence.** When the pilot metric was `INCONCLUSIVE` for insufficient samples, the evidence has no method and no adjusted confidence. A 0/1 metric then falls through to the continuous normal approximation, at confidence 0.95 whatever the gate said. Example: 8 pairs at 0.99 confidence gave a 175-trial normal-approximation plan, against 590 trials once the pilot had enough pairs. Too-small pilots are when people use this command. | `planning.py:511`, `planning.py:516` | Detect 0/1 data with `is_binary`, and recompute the confidence from the gate config and correction. |
| P1-3 | **The GitHub Action can report a stale verdict.** When `compare` exits 2, the step still reads a `comparison.json` left in the output directory and posts that old verdict to the job summary and the pull-request comment. The step itself fails correctly. | `action.yml:107` | Delete the outputs before running; read the verdict only for exit codes 0, 1 and 3. |
| P1-4 | **Fork pull requests fail with `comment: "true"`.** The token is read-only on forks, so the comment call fails the job even when the gate passes. The shipped example enables comments. | `action.yml:115-129`, `examples/github-actions/physim-gate.yml:42` | Skip or tolerate comment failures on forks, and document it. |
| P1-5 | **The LeRobot single-task layout uses one scenario id for every environment.** Without `-O scenario`, every 0.3.x `eval_info.json` gets the scenario `lerobot-eval`. A PushT baseline compared with an ALOHA candidate is then not flagged, and in the store every such scenario shares one baseline file. | `adapters/lerobot.py:96` | Require `-O scenario` for that layout, or make the placeholder id a blocking run issue. |
| P1-6 | **Paired trials with different seeds are paired silently.** When both runs record seeds and the same `trial_id` has different seeds, nothing is flagged. `docs/adapters.md` implies that seeds are checked where recorded. No wrong verdict was produced, but the pairing claim is false. | `engine.py:379-421` | Flag differing recorded seeds (blocking under `on_invalid: fail`). |

### Design risks

| # | Finding | Where |
|---|---|---|
| P1-7 | **Run and trial status never affect the verdict.** A candidate with `status: failed`, or with all 30 trials `timeout` but values present, passes with only a note. The every-trial-failed lint runs in `validate` only. | `engine.py:156-158`, `validation.py:76` |
| P1-8 | **A changed scenario version or simulator version is only a note**, including when the baseline comes from the store, which keys on the scenario id but not its version. A stale `@main` from an older scenario version is used silently. | `engine.py:138-153`, `store.py:96-112` |
| P1-9 | **Comparing a file with itself passes.** Both input hashes are recorded but never compared, and a shared `run_id` is only a note. | `cli.py:203-209`, `engine.py:154` |
| P1-10 | **`policy.on_invalid: pass` disables the scenario check** and lets a gate pass when no metric was evaluated. The same holds when every metric is `required: false`. | `engine.py:134`, `engine.py:513-517` |
| P1-11 | **The relative-margin guard only catches a baseline of exactly 0.** A signed metric whose mean is 1e-9 gets a margin of 1e-10 and fails where a mean of exactly 0 passes. | `engine.py:437`, `engine.py:451` |
| P1-12 | **Constant 0/1 data is not "deterministic".** 50 zeros against 50 zeros with a 0.01 margin is `INCONCLUSIVE` (the score interval stays about ±0.07), while `docs/comparison-semantics.md` says zero-variance samples give zero-width intervals. The behavior is right; the doc is not. | `docs/comparison-semantics.md` |
| P1-13 | **Releases are not gated on CI.** `release.yml` runs the demo but not the tests, does not depend on the CI workflow, `main` has no branch protection, and the `pypi` environment has no required reviewer. v0.3.0 itself was released only after a green CI run, by hand. | `.github/workflows/release.yml:44-46` |

## P2

| # | Kind | Finding | Where |
|---|---|---|---|
| P2-1 | Design risk | `max_missing_fraction` is checked per run, not per pair; 20% missing in each run on different trials drops 40% of pairs. | `engine.py:365-421` |
| P2-2 | Design risk | Adapter options are not recorded in the comparison, and apply to both runs, so `-O scenario=x` can make any two inputs look like the same scenario. | `comparison.py:162-166` |
| P2-3 | Design risk | Bonferroni counts every configured metric, including optional ones; this is conservative and undocumented. | `engine.py:89` |
| P2-4 | Design risk | The planner's doubling-and-bisection search can return a size a few trials above the smallest, and its docstring says "smallest". Notes about the bootstrap being "too narrow below 30 trials" predate the expanded default. | `planning.py:187`, `planning.py:303` |
| P2-5 | Confirmed defect | `report` exits with the `exit_code` stored in the file instead of deriving it from the verdict; an edited file can turn a `FAIL` into exit 0. | `cli.py:341` |
| P2-6 | Confirmed defect | A non-`SystemExit` exception inside a plugin is reported as "a bug in physim-gate" (exit 4), and stops a whole suite. | `cli.py:122-135`, `suite.py:170` |
| P2-7 | Confirmed defect | Two plugins registering the same adapter name: the first on `sys.path` wins, with no warning when it is used. | `adapters/base.py:72-83` |
| P2-8 | Confirmed defect | Markdown summaries escape `<`, `>` and `|`, but not links, images or mentions taken from result files. | `reporting/markdown.py:30` |
| P2-9 | Confirmed defect | Suite scenario names that differ only in case share an output directory on case-insensitive file systems; a scenario named `junit.xml` stops the suite. | `suite.py:64` |
| P2-10 | Design risk | The action's comment lookup accepts any comment starting with the marker, from any author, and two action calls in one pull request overwrite each other. `fail` and `comment` silently treat any value other than `true` as false. | `action.yml:124-125` |
| P2-11 | Design risk | Actions are pinned by tag, not commit; `actions/checkout` keeps credentials. | `.github/workflows/*.yml` |
| P2-12 | Hygiene | The version is written in two places with no test that they match; the sdist leaves out `scripts/error_rate_study.py` and `SECURITY.md`; PyPI README links point at `main`, not the tag. | `pyproject.toml:7`, `src/physim_gate/__init__.py:13` |
| P2-13 | Hygiene | No Windows job although the classifiers say OS independent; mypy checks `src` only; the action's `comment` and bare-runner paths never run in CI. | `.github/workflows/ci.yml` |

## Unverified hypotheses

- **Count metrics may switch interval method between comparisons** (`engine.py:244`). A count metric whose values happen to be only 0 and 1 in one comparison uses the Newcombe interval there and the bootstrap elsewhere. No case was built where this flips a verdict.
- **Suite and store paths resolve differently** (`suite.py:129`, `store.py:22`). Suite paths are relative to the suite file, but the default store is relative to the working directory. Running a suite from elsewhere could pick another store if one exists there.
- **`setup-python: false` may fail on a bare runner** (`action.yml:67`), because of a missing `python` or PEP 668.

## Verified correct

- **Wilson interval:** identical to statsmodels for n up to 1,000 at four confidence levels (`stats.py:208`).
- **Unpaired Newcombe interval:** agrees with statsmodels to 2.2e-16, including 0/n, n/n, n = 1 and 10 vs 1000.
- **Paired Newcombe interval:** agrees to 4.4e-16 with an independent implementation written from the paper, over all 2,400 tables with cells 0..6. This includes the continuity correction applied only to a positive numerator, and phi = 0 when a marginal is degenerate.
  Table (36, 12, 2, 0) gives 0.0569 to 0.3404, which matches the method-10 result for the paper's first example as recalled, but was not checked against the paper.
- **Direction handling for 0/1 metrics:** correct (`engine.py:255`).
- **Exact unpaired planner:** matches brute force to 1.8e-14 over 3,450 cases. The interval bounds are monotone in both counts for every n up to 40 at three confidence levels, so the sweep is valid. The paired sampler passes chi-square tests on 200,000 draws.
- **Expanded bootstrap:** applied to both the degradation and the excess interval. The t quantile matches scipy to 1e-8.
- **Bonferroni:** reaches both interval methods and the planner.
- **Error-rate table:** all 30 rows of the measured error-rate table agree with a 400-repetition rerun within about two standard errors.
- **Scenario and metric checks:** a different scenario id blocks by default. A metric declared in only one run, or a direction or unit mismatch, gives `INVALID`. Duplicate trial ids within a run are rejected. Paired mode with no overlap gives `INVALID`.
- **Security:**
  - All YAML is loaded safely.
  - The HTML report escaped every hostile string tried, including `</script>` in run ids, units and failure messages.
  - Store and suite names cannot leave their directories.
  - The action passes every input through environment variables, so there is no shell injection.
- **Release files:** the GitHub release files equal the PyPI files (SHA-256), PyPI attestations exist, and the sdist's tests pass on Python 3.11.

## Tests after the fixes

`python -m pytest`: 231 passed (219 before, plus 12 in `tests/test_integrity.py`).
`ruff check`, `ruff format --check` and `mypy` pass.
The twelve new tests failed on `v0.3.0`, except the two that guard intended behavior (disjoint trial ids, and omissions allowed by `max_missing_fraction`). One of them checks that YAML errors name the file instead of `<unicode string>`.

## Real-world integration: PX4 first

**Recommendation: a `px4-ulog` adapter before any ROS 2 adapter.**

### Why PX4 first

- **One log per trial.** A PX4 SITL flight produces one `.ulg` file, so one log maps to one trial. PX4's own CI already runs MAVSDK SITL tests and keeps the logs ([sitl_tests.yml](https://github.com/PX4/PX4-Autopilot/blob/main/.github/workflows/sitl_tests.yml)).
- **Standard outcome signals.** The signals that decide an outcome have standard topic names, so a default metric set needs no user code: `vehicle_land_detected`, `vehicle_status.failsafe`, `mission_result`, `vehicle_local_position` and its setpoint, and `vehicle_attitude`.
- **A small, permissive reader.** [pyulog](https://github.com/PX4/pyulog) (BSD-3-Clause, depends only on numpy) reads ULog without PX4 installed.
- **Provenance comes with the log.** The log records the PX4 git hash and release (`ver_sw`, `ver_sw_release`) and `ver_hw: PX4_SITL`.

### Why not ROS 2 first

A bag has no standard trial outcome.
Nav2's `NavigateToPose` result and feedback fields differ between distributions, and topic names depend on the robot.
A ROS 2 adapter therefore needs a user-supplied extractor from the start.
Reading bags does not need ROS: rosbag2 defaults to MCAP since Iron, and `mcap-ros2-support` reads it in pure Python.
So ROS 2 should follow as a generic `ros2-mcap` adapter where the user names the metrics.

### Prototype result

A scratch prototype read pyulog's SITL test log (`sample_px4_events.ulg`, BSD-3-Clause) and computed:

- position-tracking RMSE 0.126 m
- maximum tilt 0.60 degrees
- landed, no failsafe, mission finished

A trimmed 221 KB copy gives identical numbers and is small enough to commit as a test fixture.

Two traps showed up:

- **Preflight failsafe flags.** Several `failsafe_flags` fields are set before arming in SITL, so metrics must be computed over the armed window only.
- **No seed in the log.** ULog does not record a seed, so the seed has to come from the file name or a sidecar file.

### Minimal adapter

- **Input:** a directory with one `<trial_id>.ulg` per trial. The seed is parsed from the name (`-O seed_pattern='seed-(\d+)'`) or read from an optional `<trial_id>.json` sidecar.
- **Default metrics:**
  - `landed`, 0/1, higher is better
  - `failsafe`, 0/1, lower is better, while armed
  - `mission_finished`, 0/1, higher is better, `null` without a mission
  - `pos_rmse`, metres, lower is better
  - `max_tilt`, degrees, lower is better
  - `flight_time`, seconds
  - `log_dropout_s`, seconds, an optional data-quality guard
- **Trial status:**
  - A corrupt log is `error`.
  - Never landing or disarming in the air is `failed`.
  - A log that ends while armed is `timeout`.
  - Missing topics give `null`, never 0.
- **Provenance:** the simulator version from `ver_sw_release`; the git hash and airframe as labels; each log as an artifact with its SHA-256.
- **Packaging:** an optional extra, `physim-gate[px4]` (pyulog), imported only inside `load`, so the core stays dependency-free.
- **Custom metrics:** `-O extractor=module:function` merges user metrics with the defaults.
- **CI:** the trimmed fixture plus derived failure cases made with pyulog's writer (truncated, failsafe, corrupt), golden values for the metrics, and one end-to-end `compare`. No simulator runs in CI.

### Multi-scenario evaluation design

Built on the existing suite feature:

- **Scenario matrix:** three wind levels (SIH `SIH_WIND_N`/`SIH_WIND_E` at 0, 5 and 10 m/s) by three missions (hold, box, RTL), as nine suite scenarios. Each scenario uses the same fixed seed list for baseline and candidate, so trials pair.
- **Trial counts:** from `physim-gate plan`, a 0/1 safety metric at 95% with a 5-point margin needs:

  | Pairing | Trials per run |
  |---|---|
  | Unpaired | 320 |
  | Paired, discordance 0.04 | 180 |
  | Paired, discordance 0.02 | 129 |

  A continuous metric with paired standard deviation 0.1 and margin 0.05 needs 32.
  Binary safety metrics therefore set the cost.
  On pull requests, gate the continuous metrics on about 30 to 40 paired flights, with binary metrics as warnings.
  Gate the binary metrics on about 130 paired flights nightly.
  After the first run, use `plan --from comparison.json`.
- **Multiple testing:** the suite has no cross-scenario correction today.
  Block only on a small pre-registered set (`landed`, `failsafe`) with per-scenario confidence 1 - 0.05/k, and report everything else as `required: false`.
  A suite-level Bonferroni option would be the natural next feature.
- **Baselines:** save `@main` only from a green nightly on `main`, refresh it in its own pull request after any PX4, SITL-image or scenario change, and keep the previous one for rollback.
- **Failure modes to guard:**
  - Lockstep starvation under CI CPU contention: cap the speed factor and gate on `log_dropout_s`.
  - Unseeded noise: run a determinism audit, comparing a baseline with its own rerun.
  - Mixing PX4 versions: covered by finding P1-8.
  - Crashes that write no log: the runner writes a sidecar with `status: error`, so the trial is reported rather than dropped, which P0-1 now enforces.

Sources:

- [PX4 ULog format](https://docs.px4.io/main/en/dev_log/ulog_file_format.html)
- [PX4 MAVSDK integration tests](https://docs.px4.io/main/en/test_and_ci/integration_testing_mavsdk.html)
- [PX4 SIH](https://docs.px4.io/main/en/sim_sih/index.html)
- [pyulog on PyPI](https://pypi.org/pypi/pyulog/json)
- [MCAP as the ROS 2 default](https://foxglove.dev/blog/mcap-as-the-ros2-default-bag-format)
- [mcap-ros2-support](https://github.com/foxglove/mcap/tree/main/python/mcap-ros2-support)
- [Nav2 NavigateToPose](https://api.nav2.org/actions/jazzy/navigatetopose.html)
