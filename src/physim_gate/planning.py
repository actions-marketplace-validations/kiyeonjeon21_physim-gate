"""Sample-size planning: how many trials per run does a gate need to reach a decision?

The gate PASSes a metric only when the whole confidence interval of the degradation lies at or
below the margin, and FAILs it only when the whole interval lies above. With too few trials the
interval straddles the margin and the verdict is INCONCLUSIVE. Planning answers: given what you
expect the true degradation to be, how many trials make the intended verdict likely?

* 0/1 metrics (success, collision, ...) are evaluated exactly. Every possible pair of event
  counts is weighted by its binomial probability and decided with the same unpaired Newcombe
  score interval the comparison engine uses, so the reported verdict probabilities are exact
  for unpaired comparisons. Pairing, when the runs share trial conditions, usually needs fewer
  trials, so the unpaired answer is a safe upper bound in the common case.
* Continuous metrics use the normal approximation
  ``n = ((z_conf + z_power) * sd_diff / |margin - degradation|) ** 2``. The engine uses a
  bootstrap, so this is an estimate, and it is least reliable for small n and skewed data.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from physim_gate.schema import Direction
from physim_gate.stats import newcombe_counts

#: Tail mass ignored when enumerating binomial outcomes.
_TAIL = 1e-13


class Goal(StrEnum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class VerdictProbabilities:
    pass_: float
    fail: float
    inconclusive: float

    def of(self, goal: Goal) -> float:
        return self.pass_ if goal is Goal.PASS else self.fail


@dataclass(frozen=True)
class Plan:
    kind: str
    method: str
    goal: Goal
    margin: float
    true_degradation: float
    confidence: float
    power: float
    #: Trials per run (or matched pairs). ``None`` when more than ``max_trials`` are needed.
    trials: int | None
    max_trials: int
    #: Verdict probabilities at ``trials``: exact for 0/1 metrics, approximate otherwise.
    probabilities: VerdictProbabilities | None
    notes: list[str] = field(default_factory=list)


class PlanningError(ValueError):
    """The planning question has no answer (for example degradation equal to the margin)."""


def _goal(margin: float, true_degradation: float) -> Goal:
    if margin < 0:
        raise PlanningError("margin must be >= 0")
    if math.isclose(true_degradation, margin, rel_tol=0, abs_tol=1e-12):
        raise PlanningError(
            "the true degradation equals the margin: at the boundary neither PASS nor FAIL is "
            "expected, whatever the number of trials"
        )
    return Goal.PASS if true_degradation < margin else Goal.FAIL


def _z(probability: float) -> float:
    return statistics.NormalDist().inv_cdf(probability)


def _check_levels(confidence: float, power: float) -> None:
    if not 0.5 < confidence < 1:
        raise PlanningError("confidence must be between 0.5 and 1")
    if not 0 < power < 1:
        raise PlanningError("power must be between 0 and 1")


class _Binomial:
    """Binomial(n, p) probabilities over the range that carries all but ``_TAIL`` of the mass."""

    def __init__(self, n: int, p: float) -> None:
        self.n = n
        if p <= 0 or p >= 1:
            self.lo = self.hi = 0 if p <= 0 else n
            self.pmf = [1.0]
        else:
            sd = math.sqrt(n * p * (1 - p))
            width = 9 * sd + 10
            self.lo = max(0, math.floor(n * p - width))
            self.hi = min(n, math.ceil(n * p + width))
            log_p, log_q = math.log(p), math.log1p(-p)
            base = math.lgamma(n + 1)
            self.pmf = [
                math.exp(
                    base - math.lgamma(x + 1) - math.lgamma(n - x + 1) + x * log_p + (n - x) * log_q
                )
                for x in range(self.lo, self.hi + 1)
            ]
        self._cdf: list[float] = []
        total = 0.0
        for value in self.pmf:
            total += value
            self._cdf.append(total)

    def cdf(self, x: int) -> float:
        """P(X <= x)."""
        if x < self.lo:
            return 0.0
        if x >= self.hi:
            return 1.0
        return self._cdf[x - self.lo]

    def support(self) -> range:
        return range(self.lo, self.hi + 1)

    def prob(self, x: int) -> float:
        return self.pmf[x - self.lo]


def binary_verdict_probabilities(
    trials: int,
    baseline_bad_rate: float,
    candidate_bad_rate: float,
    margin: float,
    confidence: float,
) -> VerdictProbabilities:
    """Exact verdict probabilities for an unpaired 0/1 metric where events are bad.

    Degradation is ``candidate rate - baseline rate`` of the bad event. The interval for each
    outcome is the engine's unpaired Newcombe interval. Its upper and lower bounds increase with
    the candidate count and decrease with the baseline count, so for each baseline count the
    decisive candidate counts form a prefix (PASS) and a suffix (FAIL), found with a sweep.
    """
    n = trials
    base = _Binomial(n, baseline_bad_rate)
    cand = _Binomial(n, candidate_bad_rate)

    def upper(xb: int, xc: int) -> float:
        return newcombe_counts(xb, n, xc, n, confidence)[1]

    def lower(xb: int, xc: int) -> float:
        return newcombe_counts(xb, n, xc, n, confidence)[0]

    p_pass = p_fail = 0.0
    # PASS needs upper(xb, xc) <= margin; the largest such xc never decreases as xb grows.
    pass_edge = -1
    # FAIL needs lower(xb, xc) > margin; the smallest such xc never decreases as xb grows.
    fail_edge = 0
    for xb in base.support():
        while pass_edge + 1 <= n and upper(xb, pass_edge + 1) <= margin:
            pass_edge += 1
        while fail_edge <= n and not lower(xb, fail_edge) > margin:
            fail_edge += 1
        weight = base.prob(xb)
        p_pass += weight * (cand.cdf(pass_edge) if pass_edge >= 0 else 0.0)
        p_fail += weight * (1.0 - cand.cdf(fail_edge - 1))
    p_pass, p_fail = min(max(p_pass, 0.0), 1.0), min(max(p_fail, 0.0), 1.0)
    return VerdictProbabilities(
        pass_=p_pass, fail=p_fail, inconclusive=max(0.0, 1.0 - p_pass - p_fail)
    )


def _smallest_n(
    probability: Callable[[int], float], power: float, start: int, max_trials: int
) -> int | None:
    """Smallest n >= start with probability(n) >= power, assuming it rises with n."""
    if probability(start) >= power:
        return start
    low, high = start, start
    while probability(high) < power:
        if high >= max_trials:
            return None
        low, high = high, min(max_trials, high * 2)
    while high - low > 1:
        mid = (low + high) // 2
        if probability(mid) >= power:
            high = mid
        else:
            low = mid
    return high


def plan_binary(
    rate: float,
    margin: float,
    *,
    direction: Direction = Direction.HIGHER_IS_BETTER,
    true_degradation: float = 0.0,
    confidence: float = 0.95,
    power: float = 0.8,
    min_samples: int = 10,
    max_trials: int = 100_000,
) -> Plan:
    """Trials per run for a 0/1 metric with baseline ``rate`` (fraction of 1s)."""
    _check_levels(confidence, power)
    if not 0 <= rate <= 1:
        raise PlanningError("rate must be between 0 and 1")
    goal = _goal(margin, true_degradation)
    bad_base = rate if direction is Direction.LOWER_IS_BETTER else 1.0 - rate
    bad_cand = bad_base + true_degradation
    if not 0 <= bad_cand <= 1:
        raise PlanningError(
            f"a true degradation of {true_degradation:g} from a rate of {rate:g} leaves [0, 1]"
        )

    cache: dict[int, VerdictProbabilities] = {}

    def probabilities(n: int) -> VerdictProbabilities:
        if n not in cache:
            cache[n] = binary_verdict_probabilities(n, bad_base, bad_cand, margin, confidence)
        return cache[n]

    trials = _smallest_n(lambda n: probabilities(n).of(goal), power, min_samples, max_trials)
    notes = [
        "exact for unpaired runs; runs that share trial conditions (paired) usually need fewer",
        "power of 0/1 metrics rises unevenly with n, so a few more trials than shown add margin",
    ]
    return Plan(
        kind="binary",
        method="exact (unpaired Newcombe score interval)",
        goal=goal,
        margin=margin,
        true_degradation=true_degradation,
        confidence=confidence,
        power=power,
        trials=trials,
        max_trials=max_trials,
        probabilities=probabilities(trials) if trials is not None else None,
        notes=notes,
    )


def plan_continuous(
    stdev: float,
    margin: float,
    *,
    paired: bool = False,
    true_degradation: float = 0.0,
    confidence: float = 0.95,
    power: float = 0.8,
    min_samples: int = 10,
    max_trials: int = 100_000,
) -> Plan:
    """Trials per run for a continuous metric.

    ``stdev`` is the standard deviation of single-trial values (unpaired) or of the per-pair
    differences between candidate and baseline (``paired=True``).
    """
    _check_levels(confidence, power)
    if not stdev > 0:
        raise PlanningError("stdev must be > 0")
    goal = _goal(margin, true_degradation)
    sd_diff = stdev if paired else stdev * math.sqrt(2)
    z_conf = _z(1 - (1 - confidence) / 2)
    gap = abs(margin - true_degradation)
    needed = math.ceil(((z_conf + _z(power)) * sd_diff / gap) ** 2)
    required = max(needed, min_samples)
    trials = required if required <= max_trials else None

    probabilities = None
    if trials is not None:
        se = sd_diff / math.sqrt(trials)
        normal = statistics.NormalDist()
        p_pass = normal.cdf((margin - true_degradation) / se - z_conf)
        p_fail = normal.cdf((true_degradation - margin) / se - z_conf)
        probabilities = VerdictProbabilities(
            pass_=p_pass, fail=p_fail, inconclusive=max(0.0, 1.0 - p_pass - p_fail)
        )
    notes = ["normal approximation; the engine's bootstrap may need somewhat more trials"]
    if trials is not None and trials < 30:
        notes.append("below 30 trials the bootstrap interval is too narrow, see the error rates")
    return Plan(
        kind="continuous",
        method="normal approximation (paired)" if paired else "normal approximation (unpaired)",
        goal=goal,
        margin=margin,
        true_degradation=true_degradation,
        confidence=confidence,
        power=power,
        trials=trials,
        max_trials=max_trials,
        probabilities=probabilities,
        notes=notes,
    )
