"""The baseline-versus-candidate comparison engine.

For every gated metric the engine produces three layers that are kept separate on purpose:

1. *Numerical*: point estimates and whether the observed degradation exceeds the margin.
2. *Statistical evidence*: a bootstrap interval of the degradation, classified against the margin.
3. *Policy verdict*: PASS / FAIL / INCONCLUSIVE / INVALID, and whether it blocks the gate.

See ``docs/comparison-semantics.md`` for the full decision rules.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from physim_gate import __version__
from physim_gate.comparison import (
    ComparisonResult,
    Evidence,
    ExitCode,
    Inputs,
    MetricComparison,
    Numerical,
    PairingInfo,
    RunSummary,
    SampleSummary,
    StatisticalEvidence,
    Tool,
    Verdict,
)
from physim_gate.config import Correction, GateConfig, OnUncertain, Pairing, ResolvedMetricGate
from physim_gate.provenance import utc_now
from physim_gate.schema import (
    FAILURE_RATE_METRIC,
    Direction,
    RunResult,
    RunStatus,
    TrialStatus,
)
from physim_gate.stats import bootstrap_degradation, mean, median, stat_fn, stdev

_MAX_LISTED_IDS = 20


class _Invalid(Exception):
    """Internal: the metric cannot be compared; the message becomes the reason."""


@dataclass
class _Samples:
    baseline: list[float]
    candidate: list[float]
    baseline_ids: list[str]
    candidate_ids: list[str]
    pairing: PairingInfo


def compare_runs(
    baseline: RunResult,
    candidate: RunResult,
    config: GateConfig,
    *,
    inputs: Inputs | None = None,
    generated_at: datetime | None = None,
) -> ComparisonResult:
    """Compare two runs under a gate config and return a self-contained result."""
    run_issues, run_blocking = _check_runs(baseline, candidate, config)

    confidence_divisor = (
        len(config.metrics) if config.policy.correction is Correction.BONFERRONI else 1
    )
    metrics = [
        _compare_metric(config.resolve(name), baseline, candidate, config, confidence_divisor)
        for name in config.metrics
    ]

    declared = (set(baseline.metrics) | set(candidate.metrics)) - set(config.metrics)
    verdict, summary = _overall(metrics, run_blocking)
    return ComparisonResult(
        tool=Tool(version=__version__),
        generated_at=generated_at or utc_now(),
        inputs=inputs or Inputs(),
        config=config,
        baseline=_summarize_run(baseline),
        candidate=_summarize_run(candidate),
        run_issues=run_issues,
        run_blocking_issues=run_blocking,
        metrics=metrics,
        untracked_metrics=sorted(declared),
        verdict=verdict,
        exit_code=int(exit_code_for(verdict)),
        summary=summary,
    )


def exit_code_for(verdict: Verdict) -> ExitCode:
    if verdict is Verdict.PASS:
        return ExitCode.OK
    if verdict is Verdict.FAIL:
        return ExitCode.REGRESSION
    return ExitCode.UNCERTAIN


def _check_runs(
    baseline: RunResult, candidate: RunResult, config: GateConfig
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    blocking: list[str] = []

    if baseline.scenario.id != candidate.scenario.id:
        message = (
            f"scenario differs: baseline {baseline.scenario.id!r}, "
            f"candidate {candidate.scenario.id!r}"
        )
        if config.policy.require_same_scenario and config.policy.on_invalid is OnUncertain.FAIL:
            blocking.append(message + " (policy.require_same_scenario is true)")
        else:
            issues.append(message)
    elif baseline.scenario.version != candidate.scenario.version:
        issues.append(
            f"scenario version differs: {baseline.scenario.version!r} vs "
            f"{candidate.scenario.version!r}"
        )
    if baseline.domain != candidate.domain:
        issues.append(f"domain differs: {baseline.domain.value} vs {candidate.domain.value}")
    if baseline.simulator.name != candidate.simulator.name:
        issues.append(
            f"simulator differs: {baseline.simulator.name!r} vs {candidate.simulator.name!r}"
        )
    elif baseline.simulator.version != candidate.simulator.version:
        issues.append(
            f"simulator version differs: {baseline.simulator.version!r} vs "
            f"{candidate.simulator.version!r}; differences may come from the simulator"
        )
    if baseline.run_id == candidate.run_id:
        issues.append(f"baseline and candidate share run_id {baseline.run_id!r}")
    for label, run in (("baseline", baseline), ("candidate", candidate)):
        if run.status is not RunStatus.COMPLETED:
            issues.append(f"{label} run status is {run.status.value!r}")
    return issues, blocking


def _compare_metric(
    gate: ResolvedMetricGate,
    baseline: RunResult,
    candidate: RunResult,
    config: GateConfig,
    confidence_divisor: int,
) -> MetricComparison:
    adjusted_confidence = 1.0 - (1.0 - gate.confidence) / confidence_divisor
    base = {
        "name": gate.name,
        "required": gate.required,
        "statistic": gate.statistic,
        "min_samples": gate.min_samples,
    }
    try:
        unit, direction = _metric_identity(gate, baseline, candidate)
        base |= {"unit": unit, "direction": direction}
        samples = _collect_samples(gate, baseline, candidate)
    except _Invalid as exc:
        return _finish(
            base,
            config,
            evidence=StatisticalEvidence(outcome=Evidence.NOT_EVALUATED),
            verdict=Verdict.INVALID,
            reasons=[str(exc)],
        )

    base |= {
        "pairing": samples.pairing,
        "baseline": _summarize(samples.baseline, samples.baseline_ids, baseline, gate.name),
        "candidate": _summarize(samples.candidate, samples.candidate_ids, candidate, gate.name),
    }
    reasons: list[str] = []
    if samples.pairing.note:
        reasons.append(samples.pairing.note)

    fn = stat_fn(gate.statistic)
    b_stat = fn(samples.baseline)
    c_stat = fn(samples.candidate)
    sign = 1.0 if direction is Direction.LOWER_IS_BETTER else -1.0
    degradation = sign * (c_stat - b_stat)

    try:
        margin_fn, margin_basis = _margin(gate, b_stat)
    except _Invalid as exc:
        return _finish(
            base,
            config,
            evidence=StatisticalEvidence(outcome=Evidence.NOT_EVALUATED),
            verdict=Verdict.INVALID,
            reasons=[*reasons, str(exc)],
        )
    margin = margin_fn(b_stat)
    numerical = Numerical(
        baseline=b_stat,
        candidate=c_stat,
        delta=c_stat - b_stat,
        degradation=degradation,
        relative_degradation=degradation / abs(b_stat) if b_stat != 0 else None,
        margin=margin,
        margin_basis=margin_basis,
        exceeds_margin=degradation > margin,
    )
    base["numerical"] = numerical

    paired = samples.pairing.mode == "paired"
    n_effective = (
        len(samples.baseline) if paired else min(len(samples.baseline), len(samples.candidate))
    )
    if n_effective < gate.min_samples:
        unit_word = "matched pairs" if paired else "observations in the smaller sample"
        reasons.append(
            f"insufficient samples: {n_effective} {unit_word}, min_samples is {gate.min_samples}; "
            "no confidence interval computed"
        )
        return _finish(
            base,
            config,
            evidence=StatisticalEvidence(outcome=Evidence.INSUFFICIENT_SAMPLES),
            verdict=Verdict.INCONCLUSIVE,
            reasons=reasons,
        )

    seed = f"{gate.bootstrap_seed}:{gate.name}"
    boot = bootstrap_degradation(
        samples.baseline,
        samples.candidate,
        paired=paired,
        statistic=gate.statistic,
        sign=sign,
        margin=margin_fn,
        confidence=adjusted_confidence,
        resamples=gate.bootstrap_resamples,
        seed=seed,
    )
    excess_low, excess_high = boot.excess
    pct = _pct(adjusted_confidence)
    if excess_low > 0:
        outcome, verdict = Evidence.REGRESSION, Verdict.FAIL
        reasons.append(
            f"regression: the {pct} interval of the degradation lies entirely above the "
            f"allowed margin ({_fmt(margin)}{_unit(unit)})"
        )
    elif excess_high <= 0:
        outcome, verdict = Evidence.NON_INFERIOR, Verdict.PASS
        reasons.append(
            f"no regression: the {pct} interval of the degradation lies at or below the "
            f"allowed margin ({_fmt(margin)}{_unit(unit)})"
        )
    else:
        outcome, verdict = Evidence.INCONCLUSIVE, Verdict.INCONCLUSIVE
        observed = "exceeds" if numerical.exceeds_margin else "is within"
        reasons.append(
            f"inconclusive: the observed degradation {observed} the margin, but the {pct} "
            "interval straddles it; more trials would narrow the interval"
        )

    evidence = StatisticalEvidence(
        outcome=outcome,
        method="paired_bootstrap" if paired else "unpaired_bootstrap",
        confidence=gate.confidence,
        adjusted_confidence=adjusted_confidence,
        resamples=gate.bootstrap_resamples,
        seed=seed,
        degradation_ci=boot.degradation,
        excess_ci=boot.excess,
    )
    return _finish(base, config, evidence=evidence, verdict=verdict, reasons=reasons)


def _finish(
    base: dict[str, object],
    config: GateConfig,
    *,
    evidence: StatisticalEvidence,
    verdict: Verdict,
    reasons: list[str],
) -> MetricComparison:
    required = bool(base["required"])
    if verdict is Verdict.FAIL:
        blocking = required
    elif verdict is Verdict.INCONCLUSIVE:
        blocking = required and config.policy.on_inconclusive is OnUncertain.FAIL
    elif verdict is Verdict.INVALID:
        blocking = required and config.policy.on_invalid is OnUncertain.FAIL
    else:
        blocking = False
    if not required and verdict is not Verdict.PASS:
        reasons = [*reasons, "metric is not required, so this does not block the gate"]
    return MetricComparison.model_validate(
        {**base, "evidence": evidence, "verdict": verdict, "blocking": blocking, "reasons": reasons}
    )


def _metric_identity(
    gate: ResolvedMetricGate, baseline: RunResult, candidate: RunResult
) -> tuple[str, Direction]:
    b_spec = baseline.metric_spec(gate.name)
    c_spec = candidate.metric_spec(gate.name)
    missing = [label for label, s in (("baseline", b_spec), ("candidate", c_spec)) if s is None]
    if missing or b_spec is None or c_spec is None:
        raise _Invalid(f"metric is not declared in the {' and '.join(missing)} run")
    if b_spec.direction != c_spec.direction:
        raise _Invalid(
            f"direction differs between runs: baseline {b_spec.direction.value}, "
            f"candidate {c_spec.direction.value}"
        )
    if b_spec.unit != c_spec.unit:
        raise _Invalid(
            f"unit differs between runs: baseline {b_spec.unit!r}, candidate {c_spec.unit!r}"
        )
    if gate.direction is not None and gate.direction != b_spec.direction:
        raise _Invalid(
            f"config says {gate.direction.value} but the runs declare {b_spec.direction.value}"
        )
    return b_spec.unit, b_spec.direction


def _collect_samples(
    gate: ResolvedMetricGate, baseline: RunResult, candidate: RunResult
) -> _Samples:
    b_values = baseline.metric_values(gate.name)
    c_values = candidate.metric_values(gate.name)

    for label, values in (("baseline", b_values), ("candidate", c_values)):
        if not values:
            raise _Invalid(f"{label} run has no trials")
        n_missing = sum(1 for v in values.values() if v is None)
        fraction = n_missing / len(values)
        if fraction > gate.max_missing_fraction:
            raise _Invalid(
                f"{label}: value missing in {n_missing} of {len(values)} trials "
                f"({fraction:.0%}), above max_missing_fraction "
                f"({gate.max_missing_fraction:.0%}). Report values for failed trials too, or "
                f"raise max_missing_fraction and gate {FAILURE_RATE_METRIC} so failures stay "
                "visible"
            )

    b_keys, c_keys = set(b_values), set(c_values)
    if gate.pairing is Pairing.UNPAIRED:
        mode, note = "unpaired", None
    elif gate.pairing is Pairing.PAIRED:
        if not b_keys & c_keys:
            raise _Invalid("pairing is 'paired' but no trial_id appears in both runs")
        mode, note = "paired", None
    elif b_keys == c_keys:
        mode, note = "paired", None
    else:
        mode = "unpaired"
        note = (
            "trial_ids differ between runs, so trials are compared as independent samples "
            "(set pairing: paired to match on the shared ids instead)"
        )

    if mode == "unpaired":
        b_obs = [(k, v) for k, v in b_values.items() if v is not None]
        c_obs = [(k, v) for k, v in c_values.items() if v is not None]
        pairing = PairingInfo(mode="unpaired", note=note)
    else:
        shared = [k for k in b_values if k in c_keys]
        complete = [k for k in shared if b_values[k] is not None and c_values[k] is not None]
        b_obs = [(k, v) for k in complete if (v := b_values[k]) is not None]
        c_obs = [(k, v) for k in complete if (v := c_values[k]) is not None]
        unmatched_b = sorted(b_keys - c_keys)
        unmatched_c = sorted(c_keys - b_keys)
        dropped = len(shared) - len(complete)
        notes = []
        if unmatched_b or unmatched_c:
            notes.append(
                f"{len(unmatched_b)} baseline and {len(unmatched_c)} candidate trials have no "
                "match and are excluded"
            )
        if dropped:
            notes.append(f"{dropped} matched pairs dropped because a value is missing")
        pairing = PairingInfo(
            mode="paired",
            n_pairs=len(complete),
            unmatched_baseline=unmatched_b[:_MAX_LISTED_IDS],
            unmatched_candidate=unmatched_c[:_MAX_LISTED_IDS],
            note="; ".join(notes) or None,
        )

    if not b_obs or not c_obs:
        raise _Invalid("no usable observations in at least one run")
    return _Samples(
        baseline=[v for _, v in b_obs],
        candidate=[v for _, v in c_obs],
        baseline_ids=[k for k, _ in b_obs],
        candidate_ids=[k for k, _ in c_obs],
        pairing=pairing,
    )


def _margin(gate: ResolvedMetricGate, b_stat: float) -> tuple[Callable[[float], float], str]:
    absolute = gate.max_absolute_degradation
    relative = gate.max_relative_degradation
    if relative is not None and b_stat == 0:
        if absolute is None:
            raise _Invalid(
                "the baseline statistic is 0, so max_relative_degradation is undefined; "
                "add max_absolute_degradation for this metric"
            )
        return (
            lambda _b: absolute,
            "absolute (the relative threshold is undefined because the baseline is 0)",
        )
    if relative is None:
        assert absolute is not None
        return (lambda _b: absolute), "absolute"
    if absolute is None:
        return (lambda b: relative * abs(b)), f"relative ({relative:.4g} x |baseline|)"
    return (
        lambda b: min(absolute, relative * abs(b)),
        f"stricter of absolute ({absolute:.4g}) and relative ({relative:.4g} x |baseline|)",
    )


def _summarize(values: list[float], ids: list[str], run: RunResult, name: str) -> SampleSummary:
    all_values = run.metric_values(name)
    return SampleSummary(
        n=len(values),
        n_trials=len(all_values),
        n_missing=sum(1 for v in all_values.values() if v is None),
        mean=mean(values) if values else None,
        median=median(values) if values else None,
        stdev=stdev(values),
        min=min(values) if values else None,
        max=max(values) if values else None,
        values=values,
        trial_ids=ids,
    )


def _summarize_run(run: RunResult) -> RunSummary:
    return RunSummary(
        run_id=run.run_id,
        created_at=run.created_at,
        domain=run.domain,
        simulator=run.simulator,
        scenario=run.scenario,
        policy=run.policy,
        source=run.source,
        environment=run.environment,
        seeds=run.seeds,
        status=run.status,
        n_trials=len(run.trials),
        n_trials_not_ok=sum(1 for t in run.trials if t.status is not TrialStatus.OK),
        labels=run.labels,
    )


def _overall(metrics: list[MetricComparison], run_blocking: list[str]) -> tuple[Verdict, str]:
    blocking = [m for m in metrics if m.blocking]
    failed = [m.name for m in blocking if m.verdict is Verdict.FAIL]
    invalid = [m.name for m in blocking if m.verdict is Verdict.INVALID]
    uncertain = [m.name for m in blocking if m.verdict is Verdict.INCONCLUSIVE]
    total = len(metrics)
    if run_blocking:
        return Verdict.INVALID, "runs are not comparable: " + "; ".join(run_blocking)
    if failed:
        return Verdict.FAIL, (
            f"regression detected in {len(failed)} of {total} gated metrics: {', '.join(failed)}"
        )
    if invalid:
        return Verdict.INVALID, (
            f"{len(invalid)} of {total} gated metrics could not be compared: {', '.join(invalid)}"
        )
    if uncertain:
        return Verdict.INCONCLUSIVE, (
            f"no regression shown, but the evidence is inconclusive for {len(uncertain)} of "
            f"{total} gated metrics: {', '.join(uncertain)}"
        )
    warned = [m.name for m in metrics if m.verdict is not Verdict.PASS]
    if warned:
        return Verdict.PASS, (
            f"no blocking regression; non-blocking findings for: {', '.join(warned)}"
        )
    return Verdict.PASS, f"all {total} gated metrics show no regression beyond their margins"


def _pct(confidence: float) -> str:
    return f"{confidence * 100:.4g}%"


def _unit(unit: str) -> str:
    return f" {unit}" if unit else ""


def _fmt(value: float) -> str:
    return f"{value:.4g}"
