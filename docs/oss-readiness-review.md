# OSS readiness review (v0.1.0)

This is a critical review of PhySim Gate v0.1.0, written from the point of view of an independent maintainer and a potential user.
It was done on 2026-10-16 against tag `v0.1.0` (commit `3312d1e`).
The fixes it led to are on `main` after that tag.

Everything below is based on commands that were actually run, code that was actually read, or simulations that are reproducible from this repository.
Where something could not be verified, it says so.

## Summary

- **One critical statistical bug was found and fixed.** For 0/1 metrics such as a collision indicator, v0.1.0 could report "no regression" when the candidate was clearly worse. In simulation, a true collision-rate increase from 1% to 6% (margin 2 points, 30 trials per run) was called PASS in 15% of runs. At exactly the margin it was called PASS in 38% of runs instead of at most about 2.5%.
- **One critical packaging bug was found and fixed.** The declared dependency floor `typer>=0.12` installs a CLI that cannot parse its own options. The real floor is `typer>=0.15.4`.
- **Five smaller bugs were found and fixed.** Four affect exit codes (which CI relies on) and one affects `init`.
- **Several claims overstated the project** and have been corrected, most importantly the claimed error rate and the novelty of the statistics.
- **The differentiation is real but narrow.** The statistics are standard methods available in scipy and statsmodels. What PhySim Gate adds is the combination of a simulator-independent per-trial schema, explicit rules for pairing and missing data, a three-way verdict and CI outputs. No user has validated that this combination is worth a separate tool yet.

## Follow-up in v0.2.0

The review itself is unchanged below; this section records what happened to the open items since.

- **#14, sample-size guidance: addressed.** `physim-gate plan` computes how many trials a verdict needs, exactly for unpaired 0/1 metrics and by normal approximation for continuous ones. See [comparison-semantics.md](comparison-semantics.md#planning-the-number-of-trials).
- **#28, real simulator data: addressed.** [`examples/lerobot-pusht/`](../examples/lerobot-pusht) gates real `lerobot-eval` output through the new `lerobot` adapter, and CI runs it through the GitHub Action.
- **#23 and #24, hygiene: partly addressed.** Dependabot now covers GitHub Actions and pip, and `SECURITY.md` exists. Actions are still pinned by tag, and there is no code of conduct yet.
- **Still open:** #13 (small-sample bootstrap accuracy), #15 to #22, #25 to #27.

## How the review was done

| Area | What was done |
|---|---|
| Documented examples | Fresh clone of `v0.1.0`, fresh virtual environments on Python 3.13 and 3.11, every shell and Python example in README, CONTRIBUTING, `docs/` and `examples/` executed as written |
| Statistics | `scripts/error_rate_study.py`: the real engine on synthetic data with a known true degradation, 400 to 1,000 repetitions per scenario |
| Competitors | Shallow clones of RoboCI and robot-sim-test-harness, source read (not executed); PyPI and documentation checks for related Python tools |
| Packaging | Installs at the latest and at the lowest declared dependency versions (`uv pip install --resolution lowest-direct`), wheel and sdist builds, wheel installed into a clean environment |
| CI | GitHub Actions runs on the public repository |
| Edge cases | Hand-built inputs for directories, unreadable files, huge integers, closed pipes |

## Prioritized findings

Status: **fixed** means fixed on `main` with a regression test that fails on `v0.1.0` and passes after the fix (unless noted).
**Open** means documented here and in the docs, but not changed, to avoid expanding scope.

### P0: critical

| # | Finding | Evidence | Status |
|---|---|---|---|
| 1 | **False PASS for 0/1 metrics with few or no events.** A percentile bootstrap of a sample with 0 events has zero width, so "0 collisions in 30 trials" was treated as a rate known to be exactly 0. | Error-rate study at 400 repetitions, collision rate 0.01 to 0.06, margin 0.02, 30 trials: PASS 15.0%. At the margin (0.01 to 0.03): PASS 38.3% at 30 trials, 10.0% at 80, 6.5% at 300. | **Fixed.** 0/1 metrics (statistic `mean`, all values exactly 0 or 1) now use Newcombe score intervals: hybrid score for unpaired data, Newcombe's paired method for matched trials. After the fix, the same 1% to 6% scenario gives PASS 0% at 30, 80 and 300 trials (1,000 repetitions), and errors at the margin are 0 to 3.2%. The unpaired implementation reproduces Newcombe's published example (0.0524, 0.3339). Tests: `tests/test_engine.py` (binary section), `tests/test_stats.py`. |
| 2 | **Declared dependency floor does not work.** `typer>=0.12` was never tested. | `uv pip install --resolution lowest-direct` on Python 3.11 with the current click (8.5): typer 0.12 fails every CLI test that parses options ("Type not yet supported" for annotations such as `Path \| None`), 0.13.1 to 0.15.0 fail 14 of the 16 CLI tests, 0.15.1 and 0.15.3 fail 18 tests in the full suite (enum option defaults), and 0.15.4 passes everything. PyYAML 6.0 publishes no wheels for Python 3.12 or 3.13 (checked on PyPI), so it would be built from source there. | **Fixed.** `typer>=0.15.4`, `pyyaml>=6.0.1` (the first release with Python 3.12 wheels). New CI job `minimum-versions` installs the lowest allowed versions on Python 3.11 and runs the suite. |

### P1: wrong exit codes and crashes

Exit codes are the interface CI depends on, so these matter more than their size suggests.

| # | Finding | Evidence | Status |
|---|---|---|---|
| 3 | A directory as `--baseline` or `--candidate` (documented as allowed for adapters) crashed with exit 4 while hashing the input. | Reproduced with a stub adapter: `IsADirectoryError` in `sha256_file`. | **Fixed.** Directories get a deterministic content hash over relative paths and file hashes. Test: `test_directory_inputs_are_hashed_not_crashed`. |
| 4 | `physim-gate compare ... \| head -1` exited 1 ("regression") whatever the verdict. | Rich's `Console.on_broken_pipe` raises `SystemExit(1)`. A passing or inconclusive gate exited 1. | **Fixed.** The console stops writing without exiting, so the verdict's code survives. Test: `test_exit_code_survives_a_closed_stdout`. |
| 5 | An integer too large for a float (for example 10^400) in a metric value caused an internal error (exit 4) with a traceback in both `validate` and `compare`. | `OverflowError` in pydantic's non-finite check. | **Fixed.** Rejected as a schema error with "number is too large to represent as a float". Test: `test_integers_too_large_for_a_float_are_rejected`. |
| 6 | `validate` exited 1 ("invalid document") for unreadable input such as a directory, while the README documents exit 2 for unreadable input. | `physim-gate validate examples/highway-merge/` exited 1. | **Fixed.** New `InputError` (a `SchemaError`) for unreadable input, mapped to exit 2 everywhere. Test: `test_validate_unreadable_input_exits_2`. |
| 7 | `init --adapter X --from Y` could not pass adapter options, so `init` was unusable with adapters that need them (the error message even suggested `-O`, which did not exist). | `physim-gate init --adapter csv --from ...` exited 2 with "No such option: -O". | **Fixed.** `init` accepts `-O key=value`. Test: `test_init_passes_adapter_options`. |

### P1: misleading or unsupported claims

| # | Claim | Problem | Status |
|---|---|---|---|
| 8 | "Deciding on one side ... corresponds to a one-sided error rate of about 2.5%." | Measured per-side error at the margin is about 2-3.5% with 30 or more trials, but up to 5.5% with 10 trials (the default `min_samples`). | **Fixed in docs.** `docs/comparison-semantics.md` now publishes the measured table and how to reproduce it. |
| 9 | README: "Statistically defensible gates" as a differentiator. | Not true for 0/1 metrics before the fix, and the methods are standard, not novel. | **Fixed in docs.** Reworded to "uncertainty-aware gates built on standard methods", with scipy, statsmodels, rliable, asv and Bencher named. |
| 10 | `docs/competitive-landscape.md`: zero-baseline semantics as a differentiator; robot-sim-test-harness has a "simulator base class"; competitor statistics "not described"; LeRobot's Wilson interval "unverified". | Reading the source showed RoboCI and robot-sim-test-harness both handle zero baselines; the harness uses a `register()` registry; neither has statistics (verified absent, not merely undocumented); LeRobot's Wilson interval is in `lerobot/utils/eval_stats.py`. | **Fixed in docs.** The page now cites code locations and adds a section on existing Python tools. |
| 11 | README sample terminal output, adapter example, missing Gymnasium example docs, example workflow. | The sample omitted two printed lines; the adapter sample silently accepted unknown options although `docs/adapters.md` says to reject them; `examples/gymnasium/` had no README; the example workflow copied into a directory that might not exist. | **Fixed.** |
| 12 | The no-regression demo and the CSV example "passed". | After finding 1, both were shown to rely on the zero-width bootstrap (the CSV `success` metric passed with an interval of exactly [0, 0] from 40 trials). | **Fixed.** The synthetic fixtures now use more trials (300 and 200) and pass on honest intervals. |

### P2: open statistical and design issues

| # | Issue | Why it matters | Suggested action |
|---|---|---|---|
| 13 | The percentile bootstrap is anti-conservative at small n: about 4-5.5% per side at 10 trials, versus 2.5% nominal. | `min_samples` defaults to 10. | Raise the default to 20 or 30, or evaluate BCa or studentized intervals with the existing study script. |
| 14 | Rare events need hundreds of trials to reach any verdict. | Correct behavior, but users will see `INCONCLUSIVE` and not know what to do. | Sample-size guidance ("how many trials for this margin?"), before adding features. |
| 15 | For 0/1 metrics with a relative margin, the margin is computed once from the observed baseline rate (plug-in), not propagated. | Small extra uncertainty is ignored. | Document (done) or recommend absolute margins for rates. |
| 16 | The HTML chart draws the degradation interval against the point margin, but with relative margins the decision uses the separately computed excess interval. | The picture can disagree slightly with the verdict near the margin. | Draw the excess interval, or label the chart. |
| 17 | The paired Newcombe interval is validated by simulation only. | No published worked example was checked. | Add a test against an independent implementation. |
| 18 | The statistics are reimplemented rather than calling scipy or statsmodels. | It keeps the install small (3 runtime dependencies) but adds maintenance and risk. | Add an optional test that cross-checks against scipy and statsmodels when installed. |
| 19 | `physim-gate init` writes `max_absolute_degradation: 0.0` for continuous metrics with a zero mean. | With any noise, such a metric can almost never PASS. | Emit a commented placeholder that forces the user to choose. |
| 20 | Exit code 1 means "regression" for `compare` but "invalid file" for `validate`. | Easy to misread in scripts. | Keep for 0.x but consider distinct codes before 1.0. |
| 21 | `policy.require_same_scenario` only blocks when `on_invalid` is `fail`. | Two settings interact in a non-obvious way. | Document more prominently or decouple. |
| 22 | Only per-trial scalar metrics. | Trajectory-level regressions must be reduced to scalars by the user. | As in the roadmap; do not add before there is demand. |

### P3: project hygiene

| # | Issue | Suggested action |
|---|---|---|
| 23 | GitHub Actions are pinned by major tag, not commit SHA, and there is no Dependabot configuration. | Add Dependabot for actions and pip; consider SHA pinning. |
| 24 | No `SECURITY.md` or code of conduct. | Add both before inviting outside contributions. |
| 25 | `mypy` runs on `src/` only, not on tests or scripts. | Extend gradually. |
| 26 | The JSON Schema snapshot test depends on the pydantic version used to generate it. | It is excluded from the minimum-versions job; regenerate schemas when bumping pydantic. |
| 27 | Provenance does not hash the policy or checkpoint under test. RoboCI hashes its agent, suite and dataset. | Let adapters record artifact hashes (the schema already has `artifacts[].sha256`). |
| 28 | All bundled data is synthetic. The Gymnasium CartPole example is the only real rollout data. | A worked example on real simulator output (for example LeRobot evaluation output) would do more for credibility than any feature. |

## Statistical review

**Correctness.** The decision rule (an interval of the difference, compared against a margin, with three outcomes) is standard non-inferiority and superiority logic, and it avoids the common mistake of judging overlap of two separate intervals.
The direction handling, the stricter-of-two margins, the zero-baseline rule and the propagation of relative margins through bootstrap replicates were checked by tests and behaved as documented.
The main defect was the choice of method for 0/1 data (finding 1), now fixed.

**Reproducibility.** The bootstrap is seeded per metric from the config, so a comparison is bit-for-bit repeatable on the same Python version.
The docs-check run confirmed byte-identical JSON, HTML and JUnit outputs across runs with `SOURCE_DATE_EPOCH` set.
The synthetic fixtures are pinned by a test that runs on Linux and macOS with Python 3.11 to 3.13 in CI.
Bootstrap outputs themselves are not pinned by a golden test across platforms, so cross-platform identity of intervals is expected but not proven.

**Explanation.** Every verdict carries its reasons, the interval, the margin and the method in all output formats.
The weakest part of the explanation was the claimed error rate, now replaced by measured numbers.

Measured error rates after the fixes (1,000 repetitions each; full table in `docs/comparison-semantics.md`):

| Scenario | Trials | Wrong PASS | Wrong FAIL |
|---|---|---|---|
| Normal, at margin | 10 / 30 / 100 | 3.8% / 2.6% / 1.9% | 4.2% / 3.0% / 2.8% |
| Lognormal, relative margin, at margin | 10 / 30 / 100 | 5.5% / 3.0% / 3.4% | 5.4% / 3.5% / 2.5% |
| Success rate, at margin, unpaired | 30 / 100 / 300 | 2.4% / 3.2% / 2.9% | 2.5% / 2.8% / 2.1% |
| Collision 1% to 6% (true regression) | 30 / 80 / 300 | 0% / 0% / 0.1% | n/a (correct verdict) |

## Comparison with existing tools

| | RoboCI | robot-sim-test-harness | scipy / statsmodels / rliable | asv / Bencher | PhySim Gate |
|---|---|---|---|---|---|
| Runs simulators | yes | yes (kinematic mock) | no | runs benchmarks | no |
| Regression rule | percent change of one aggregate per run, fixed direction lists | absolute and relative tolerance on single values, two-sided | any test you write | statistical tests on timing samples | interval of the per-trial difference vs a margin, three outcomes |
| Uncertainty | none (verified in code) | none (verified in code) | yes | yes | yes |
| Missing or failed trials | skipped silently | skipped silently | n/a | n/a | invalidate the metric unless allowed; failure rate gateable |
| Paired trials | no | no | yes (as methods) | no | yes |
| Zero baselines | handled | handled | n/a | n/a | handled |
| Outputs | JSON, HTML, Markdown, dashboard | JUnit, JSON, HTML | none | HTML, server | JSON, JUnit, HTML, exit codes |
| Plugins | hardcoded registry | `register()` registry | n/a | n/a | entry points |
| Provenance | git state plus hashes of agent, suite, dataset | simulator name, seeds | n/a | machine, commit | simulator, seeds, commit, environment, input hashes |

**Is the differentiation meaningful?**
Partly.
PhySim Gate is not a reimplementation of RoboCI or robot-sim-test-harness: both are scenario runners whose gates have no notion of uncertainty and silently skip missing data.
But its statistics are a reimplementation of textbook methods available in scipy and statsmodels, and asv and Bencher already ship statistical regression gates for benchmarks.
The defensible value is the glue: a schema that any simulator can write, pairing by trial condition, rules that keep failures visible, a verdict vocabulary that includes "inconclusive", and outputs CI understands.
That is useful but small, and it has not been tested with real users.

## Packaging, tests, CI, security, docs, contributor experience

| Area | State after this review |
|---|---|
| Packaging | Hatchling, `src/` layout, `py.typed`, wheel and sdist build; wheel installs and runs the demo in a clean environment; dependency floors now tested in CI. Not on PyPI. |
| Tests | 151 tests, 96% line coverage; runtime about 6 seconds; optional integrations skip cleanly when not installed. |
| CI | Lint, format, strict mypy, tests on Ubuntu and macOS with Python 3.11 to 3.13, minimum versions, optional extras (Gymnasium, example plugin), package build plus clean-install demo. |
| Security | YAML is parsed with `safe_load`; HTML output escapes all input (tested); no network access. Adapters and the Gymnasium policy loader execute code by design; this is now documented. No `SECURITY.md` yet. |
| Docs | Every documented example was executed; all relative links resolve; the issues found are listed above and fixed. |
| Contributor experience | Setup is one `pip install -e ".[dev]"`; checks are documented; issue templates exist; the changelog is generated. Missing: pre-commit config, code of conduct, security policy. |

## Smallest improvements that would make this useful to real robotics developers

In order of expected value per unit of work:

1. **A LeRobot `eval_info.json` adapter**, validated against real output. It turns PhySim Gate from "write your own JSON" into "point it at the file you already have".
2. **Sample-size guidance**: a short table or helper that answers "how many episodes do I need to decide a 2-point margin on a 90% success rate?". Without it, rare-event users will meet `INCONCLUSIVE` and give up.
3. **A Markdown summary for pull-request comments.** Most robotics CI results are read in the PR, not in an artifact.
4. **A PyPI release and a small reusable GitHub Action**, so adoption does not require a git URL.
5. **One end-to-end example on real simulator output**, replacing synthetic data as the first thing a visitor sees.

Each of these is narrow, and none requires new statistics.

## Reproducing this review

```bash
python -m pip install -e ".[dev]"
python -m pytest                                     # includes the regression tests named above
python scripts/error_rate_study.py --reps 1000       # measured error rates (several minutes)
uv pip install --resolution lowest-direct -e . "pytest>=8"   # in a fresh Python 3.11 env
python -m pytest -W ignore::DeprecationWarning \
  --deselect tests/test_schema.py::test_published_json_schema_is_current
```
