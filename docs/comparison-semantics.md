# Comparison semantics

This document specifies how `physim-gate compare` turns two run results and a gate config into verdicts.
It is the contract: if the code disagrees with this document, that is a bug.

## Terms

- **Run**: one run result document, a batch of trials of one policy on one scenario.
- **Trial**: one episode. It has a `trial_id`, a status and per-metric values.
- **Gated metric**: a metric listed under `metrics:` in `gate.yaml`.
- **Statistic**: `mean` (default) or `median` of a metric's trial values.
- **Degradation**: `candidate - baseline` for `lower_is_better` metrics, and `baseline - candidate` for `higher_is_better` metrics.
  Positive degradation always means the candidate is worse.
- **Margin**: the largest degradation that is acceptable.

## Step 1: run-level checks

Before looking at metrics, the two runs are checked for comparability.

| Check | Effect |
|---|---|
| `scenario.id` differs | Blocking (overall `INVALID`) when `policy.require_same_scenario` is true (default) and `policy.on_invalid` is `fail`. Otherwise a note |
| `scenario.version`, `domain`, simulator name or version differ | Note only |
| Same `run_id` in both runs | Note only (often a copy-paste mistake) |
| Run `status` is not `completed` | Note only |

Notes appear in every report under "run notes".

## Step 2: metric identity

For each gated metric:

1. Both runs must declare it (or it must be the built-in `physim.failure_rate`). Otherwise `INVALID`.
2. Both runs must declare the same `direction` and `unit`. Otherwise `INVALID`.
3. If the config sets `direction`, it must match the runs. Otherwise `INVALID`. This guards against a silently flipped direction.

## Step 3: collecting samples

Values come from **all** trials, regardless of status.
An absent key and an explicit `null` are both *missing*.
`0` is a valid value.

**Missing values.** If the fraction of missing values in either run exceeds `max_missing_fraction` (default `0.0`), the metric is `INVALID`.
The default is strict on purpose: if crashed trials simply dropped out, a policy that crashes more could look better.
To tolerate missing values, raise `max_missing_fraction` and gate `physim.failure_rate` so failures stay visible.

**Pairing.** `pairing` controls whether trials are matched by `trial_id`:

| `pairing` | Behavior |
|---|---|
| `auto` (default) | Paired if both runs have exactly the same set of `trial_id`s. Otherwise unpaired, with a note |
| `paired` | Paired on the `trial_id`s present in both runs. Unmatched trials are excluded and listed. `INVALID` if no ids overlap |
| `unpaired` | Two independent samples |

In paired mode, a pair is used only if both values are present.

A `trial_id` names the trial *condition*, for example "scenario variant 3, seed 1042".
Pairing says that both runs faced the same condition.
It does **not** assume the rollouts are identical: GPU reductions, multithreaded physics, asynchronous sensors and learned policies can all diverge from the same seed.
Pairing simply removes the part of the variation that comes from the shared condition, which usually makes the comparison much more sensitive.

## Step 4: point estimates (the numerical layer)

- `baseline`, `candidate`: the statistic of each sample.
- `delta = candidate - baseline`.
- `degradation`: `delta` signed by direction (positive is worse).
- `relative_degradation = degradation / |baseline|`, or `null` when the baseline statistic is 0.
- `margin`: see below.
- `exceeds_margin = degradation > margin`.

`exceeds_margin` is reported for transparency but never decides the verdict on its own.

### Margins

Each gated metric needs at least one of:

- `max_absolute_degradation`: in the metric's unit, `>= 0`.
- `max_relative_degradation`: a fraction of `|baseline statistic|`, `>= 0`. `0.05` means 5%.

If both are set, the **stricter** (smaller) margin applies.

**Baseline of zero.** A relative margin of a zero baseline is zero, which would turn any increase into a regression.
So when the baseline statistic is exactly 0:

- if an absolute margin is also set, it is used alone, and the report says so,
- otherwise the metric is `INVALID`, with a message asking for an absolute margin.

Set `max_absolute_degradation: 0` to tolerate no degradation at all.

## Step 5: statistical evidence

If the number of usable observations is below `min_samples` (default 10), no interval is computed and the evidence is `insufficient_samples`.
For paired comparisons the count is the number of pairs; for unpaired comparisons it is the size of the smaller sample.

Otherwise a percentile bootstrap estimates the uncertainty of the degradation:

- **Paired:** resample pair indices with replacement, keeping each pair together, and recompute the statistic of each side.
- **Unpaired:** resample each run's values independently.
- Each replicate computes `degradation*` and `excess* = degradation* - margin*`.
  For relative margins, `margin*` is recomputed from the replicate's baseline statistic, so uncertainty in the baseline's magnitude is propagated.
- `bootstrap_resamples` (default 10,000) replicates are drawn from a pseudo-random generator seeded with `"<bootstrap_seed>:<metric name>"`. Results are therefore reproducible across machines.
- The two-sided interval at `confidence` (default 0.95) is taken from the replicate quantiles (linear interpolation).

The interval is always computed for the **difference** between the runs.
PhySim Gate never compares two separate per-run intervals for overlap, because overlap is not a valid significance test in general.

The evidence is classified against the margin using the `excess` interval `[lo, hi]`:

| Condition | Evidence | Verdict |
|---|---|---|
| `lo > 0` | `regression` | `FAIL` |
| `hi <= 0` | `non_inferior` | `PASS` |
| otherwise | `inconclusive` | `INCONCLUSIVE` |

Deciding on one side of a two-sided interval at confidence `c` corresponds to a one-sided error rate of about `(1 - c) / 2`, so about 2.5% at the default 0.95.
This is approximate; see the limitations below.

**Multiple metrics.** With `policy.correction: bonferroni`, each metric uses confidence `1 - (1 - c) / k`, where `k` is the number of gated metrics.
This widens every interval, which makes both `FAIL` and `PASS` harder to reach.
The default is `none`.

**Zero variance.** If both samples are constant (a fully deterministic simulator), every replicate is identical and the interval has zero width.
The comparison then reduces to an exact check of the degradation against the margin, which is the correct behavior for deterministic results.

## Step 6: policy

Each metric verdict is `blocking` if the metric is `required` (default true) and:

| Verdict | Blocking when |
|---|---|
| `FAIL` | always |
| `INCONCLUSIVE` | `policy.on_inconclusive: fail` (default) |
| `INVALID` | `policy.on_invalid: fail` (default) |
| `PASS` | never |

Non-required metrics are evaluated and reported, but never block.

The overall verdict, in order of precedence:

1. `INVALID` if there is a blocking run-level issue,
2. `FAIL` if any blocking metric is `FAIL`,
3. `INVALID` if any blocking metric is `INVALID`,
4. `INCONCLUSIVE` if any blocking metric is `INCONCLUSIVE`,
5. `PASS` otherwise.

Exit codes: `PASS` is 0, `FAIL` is 1, `INCONCLUSIVE` and `INVALID` are 3.
Exit code 2 is reserved for errors that prevent a comparison (bad arguments, unreadable or invalid files), and 4 for internal errors.

## Why the default is conservative

The default answers "is the candidate shown to be no worse than the margin?", not "is the candidate shown to be worse?".
A gate built on the second question passes whenever the data is too noisy to show anything, which is exactly when a regression is most likely to slip through.
Teams that prefer to block only on demonstrated regressions can set `policy.on_inconclusive: pass` explicitly, and the reports will still flag every inconclusive metric.

## Limitations

- Percentile bootstrap intervals undercover with small samples, skewed distributions and rare binary events. `min_samples` is a floor, not a guarantee of accuracy. Gating a 1% event rate with confidence needs hundreds or thousands of trials.
- The median's bootstrap distribution is coarse for small samples.
- Trials are assumed exchangeable within a run. Strong drift within a run (for example thermal effects in hardware-in-the-loop) violates this.
- Repeating a comparison until it passes ("optional stopping") invalidates the stated error rates.
- Statistical evidence does not establish practical relevance. The margin encodes that, and it is your decision.

## Configuration reference

```yaml
version: 1
defaults:
  statistic: mean              # mean | median
  confidence: 0.95             # in (0.5, 1)
  min_samples: 10              # >= 2
  pairing: auto                # auto | paired | unpaired
  max_missing_fraction: 0.0    # 0..1
  bootstrap_resamples: 10000   # 1000..1000000
  bootstrap_seed: 0
policy:
  on_inconclusive: fail        # fail | pass
  on_invalid: fail             # fail | pass
  correction: none             # none | bonferroni
  require_same_scenario: true
metrics:
  <name>:
    max_absolute_degradation: <float >= 0>   # at least one of these two
    max_relative_degradation: <float >= 0>
    direction: higher_is_better | lower_is_better   # optional cross-check
    required: true
    statistic / confidence / min_samples / pairing / max_missing_fraction   # per-metric overrides
```

`physim-gate schema config` prints the JSON Schema of this file.
