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
