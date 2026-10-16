"""Small, dependency-free statistics used by the comparison engine.

Everything here is deterministic for a given seed, so a comparison can be reproduced exactly
from its inputs. The bootstrap uses Python's Mersenne Twister seeded with a string, which is
stable across platforms and Python versions.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from physim_gate.config import Statistic

StatFn = Callable[[Sequence[float]], float]


def mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def median(values: Sequence[float]) -> float:
    return float(statistics.median(values))


def stat_fn(statistic: Statistic) -> StatFn:
    return mean if statistic is Statistic.MEAN else median


def stdev(values: Sequence[float]) -> float | None:
    """Sample standard deviation, or ``None`` with fewer than two values."""
    return statistics.stdev(values) if len(values) >= 2 else None


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (Hyndman and Fan type 7) of pre-sorted values."""
    if not sorted_values:
        raise ValueError("quantile of an empty sequence")
    position = (len(sorted_values) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


Interval = tuple[float, float]


@dataclass(frozen=True)
class BootstrapResult:
    """Percentile bootstrap intervals for a degradation estimate.

    ``degradation`` is the interval of ``sign * (stat(candidate) - stat(baseline))``, so positive
    values are worse. ``excess`` is the interval of the degradation minus the allowed margin, with
    the margin recomputed in each replicate when it depends on the baseline (relative thresholds).
    The gate decides on ``excess``: the entire interval above zero is evidence of a regression,
    the entire interval at or below zero is evidence of non-inferiority.
    """

    degradation: Interval
    excess: Interval


def bootstrap_degradation(
    baseline: Sequence[float],
    candidate: Sequence[float],
    *,
    paired: bool,
    statistic: Statistic,
    sign: float,
    margin: Callable[[float], float],
    confidence: float,
    resamples: int,
    seed: str,
) -> BootstrapResult:
    """Bootstrap the degradation of ``candidate`` relative to ``baseline``.

    Paired: trial indices are resampled jointly, preserving the pairing. Unpaired: each sample is
    resampled independently. The interval is two-sided at ``confidence``. Deciding on one side
    of it gives a one-sided error rate of about ``(1 - confidence) / 2``.
    """
    if paired and len(baseline) != len(candidate):
        raise ValueError("paired samples must have equal length")
    if not baseline or not candidate:
        raise ValueError("bootstrap needs non-empty samples")

    rng = random.Random(seed)
    fn = stat_fn(statistic)
    degradations: list[float] = []
    excesses: list[float] = []

    if paired:
        indices = range(len(baseline))
        n = len(baseline)
        for _ in range(resamples):
            pick = rng.choices(indices, k=n)
            b = fn([baseline[i] for i in pick])
            c = fn([candidate[i] for i in pick])
            d = sign * (c - b)
            degradations.append(d)
            excesses.append(d - margin(b))
    else:
        nb, nc = len(baseline), len(candidate)
        for _ in range(resamples):
            b = fn(rng.choices(baseline, k=nb))
            c = fn(rng.choices(candidate, k=nc))
            d = sign * (c - b)
            degradations.append(d)
            excesses.append(d - margin(b))

    alpha = 1.0 - confidence
    degradations.sort()
    excesses.sort()
    return BootstrapResult(
        degradation=(quantile(degradations, alpha / 2), quantile(degradations, 1 - alpha / 2)),
        excess=(quantile(excesses, alpha / 2), quantile(excesses, 1 - alpha / 2)),
    )


def is_binary(values: Sequence[float]) -> bool:
    """True when every value is exactly 0 or 1 (success flags, collision indicators, ...)."""
    return all(v in (0.0, 1.0) for v in values)


def wilson_interval(successes: int, n: int, z: float) -> Interval:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        raise ValueError("wilson_interval needs n > 0")
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def newcombe_difference(
    baseline: Sequence[float],
    candidate: Sequence[float],
    *,
    paired: bool,
    confidence: float,
) -> Interval:
    """Score interval for ``mean(candidate) - mean(baseline)`` of 0/1 data.

    Unpaired: Newcombe's hybrid score interval (Newcombe 1998, Stat Med 17:873, method 10).
    Paired: Newcombe's interval for paired proportions (Newcombe 1998, Stat Med 17:2635,
    method 10), which combines the Wilson intervals of both rates with the continuity-corrected
    correlation of the pairs.

    Unlike a percentile bootstrap, these intervals stay wide when a sample has no events at all,
    so "0 collisions in 30 trials" is not mistaken for a collision rate known to be zero.
    """
    if not baseline or not candidate:
        raise ValueError("newcombe_difference needs non-empty samples")
    z = statistics.NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    nb, nc = len(baseline), len(candidate)
    xb, xc = round(sum(baseline)), round(sum(candidate))
    pb, pc = xb / nb, xc / nc
    lb, ub = wilson_interval(xb, nb, z)
    lc, uc = wilson_interval(xc, nc, z)

    phi = 0.0
    if paired:
        if nb != nc:
            raise ValueError("paired samples must have equal length")
        both = sum(1 for b, c in zip(baseline, candidate, strict=True) if b == 1 and c == 1)
        base_only = xb - both
        cand_only = xc - both
        neither = nb - both - base_only - cand_only
        denominator = math.sqrt(xb * (nb - xb) * xc * (nc - xc))
        if denominator > 0:
            cross = float(both * neither - base_only * cand_only)
            if cross > 0:
                cross = max(cross - nb / 2, 0.0)
            phi = cross / denominator

    difference = pc - pb
    lower_sq = (pc - lc) ** 2 - 2 * phi * (pc - lc) * (ub - pb) + (ub - pb) ** 2
    upper_sq = (uc - pc) ** 2 - 2 * phi * (uc - pc) * (pb - lb) + (pb - lb) ** 2
    return (
        max(-1.0, difference - math.sqrt(max(lower_sq, 0.0))),
        min(1.0, difference + math.sqrt(max(upper_sq, 0.0))),
    )
