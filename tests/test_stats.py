from __future__ import annotations

import pytest

from physim_gate.config import Statistic
from physim_gate.stats import (
    bootstrap_degradation,
    is_binary,
    mean,
    median,
    newcombe_difference,
    quantile,
    stdev,
    wilson_interval,
)


def test_basic_statistics() -> None:
    assert mean([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert median([5.0, 1.0, 3.0]) == 3.0
    assert stdev([1.0]) is None
    assert stdev([1.0, 3.0]) == pytest.approx(2**0.5)


def test_quantile_interpolates() -> None:
    values = [0.0, 10.0, 20.0, 30.0, 40.0]
    assert quantile(values, 0.0) == 0.0
    assert quantile(values, 1.0) == 40.0
    assert quantile(values, 0.5) == 20.0
    assert quantile(values, 0.125) == pytest.approx(5.0)
    with pytest.raises(ValueError, match="empty"):
        quantile([], 0.5)


def boot(**kwargs: object) -> object:
    params: dict[str, object] = {
        "paired": False,
        "statistic": Statistic.MEAN,
        "sign": 1.0,
        "margin": lambda _b: 0.0,
        "confidence": 0.95,
        "resamples": 2000,
        "seed": "s",
    }
    params.update(kwargs)
    return bootstrap_degradation(**params)  # type: ignore[arg-type]


def test_interval_contains_true_shift_for_clean_data() -> None:
    baseline = [float(i % 7) for i in range(70)]
    candidate = [v + 2.0 for v in baseline]
    result = boot(baseline=baseline, candidate=candidate, paired=True)
    assert result.degradation == pytest.approx((2.0, 2.0))  # type: ignore[attr-defined]


def test_sign_flips_direction() -> None:
    baseline = [1.0, 2.0, 3.0] * 10
    candidate = [v + 1 for v in baseline]
    result = boot(baseline=baseline, candidate=candidate, paired=True, sign=-1.0)
    assert result.degradation == pytest.approx((-1.0, -1.0))  # type: ignore[attr-defined]


def test_relative_margin_is_recomputed_per_replicate() -> None:
    baseline = [10.0, 20.0] * 20
    candidate = list(baseline)
    result = boot(
        baseline=baseline, candidate=candidate, paired=True, margin=lambda b: 0.1 * abs(b)
    )
    low, high = result.excess  # type: ignore[attr-defined]
    # Paired identical samples: zero degradation, but the margin follows the resampled baseline.
    assert high > low
    assert high < 0


def test_input_checks() -> None:
    with pytest.raises(ValueError, match="equal length"):
        boot(baseline=[1.0, 2.0], candidate=[1.0], paired=True)
    with pytest.raises(ValueError, match="non-empty"):
        boot(baseline=[], candidate=[1.0])


def test_newcombe_unpaired_matches_published_example() -> None:
    # Newcombe (1998), Stat Med 17:873, example (a): 56/70 vs 48/80, method 10.
    baseline = [1.0] * 48 + [0.0] * 32
    candidate = [1.0] * 56 + [0.0] * 14
    low, high = newcombe_difference(baseline, candidate, paired=False, confidence=0.95)
    assert (round(low, 4), round(high, 4)) == (0.0524, 0.3339)


def test_newcombe_stays_wide_with_zero_events() -> None:
    zeros = [0.0] * 30
    low, high = newcombe_difference(zeros, zeros, paired=False, confidence=0.95)
    # 0 of 30 is compatible with a true rate of about 11%, so the difference is uncertain.
    assert low < -0.1
    assert high > 0.1
    low, high = newcombe_difference(zeros, zeros, paired=True, confidence=0.95)
    assert high > 0.05


def test_newcombe_paired_uses_the_pairing() -> None:
    baseline = [1.0] * 20 + [0.0] * 20
    candidate = [1.0] * 18 + [0.0] * 2 + [0.0] * 20  # 2 discordant pairs only
    paired = newcombe_difference(baseline, candidate, paired=True, confidence=0.95)
    unpaired = newcombe_difference(baseline, candidate, paired=False, confidence=0.95)
    assert paired[0] <= -0.05 <= paired[1]
    assert paired[1] - paired[0] < unpaired[1] - unpaired[0]


def test_wilson_and_binary_helpers() -> None:
    low, high = wilson_interval(0, 30, 1.959964)
    assert low == 0.0
    assert high == pytest.approx(0.1135, abs=1e-4)
    assert is_binary([0.0, 1.0, 1.0])
    assert not is_binary([0.0, 0.5])
    with pytest.raises(ValueError, match="n > 0"):
        wilson_interval(0, 0, 1.96)


@pytest.mark.parametrize(("nb", "nc"), [(1000, 10), (10, 1000), (10, 10), (40, 60)])
def test_expanded_bootstrap_matches_welch_width_for_unequal_samples(nb: int, nc: int) -> None:
    """The expanded interval should be about as wide as Welch's t interval.

    With a large baseline and a small candidate the uncertainty comes almost entirely from the
    candidate, so the t quantile needs about n_candidate - 1 degrees of freedom, not
    n_baseline + n_candidate - 2.
    """
    import random
    import statistics

    from physim_gate.stats import _t_quantile

    rng = random.Random(f"welch-{nb}-{nc}")
    baseline = [rng.gauss(0, 1) for _ in range(nb)]
    candidate = [rng.gauss(0, 1) for _ in range(nc)]
    vb = statistics.variance(baseline) / nb
    vc = statistics.variance(candidate) / nc
    df = (vb + vc) ** 2 / (vb**2 / (nb - 1) + vc**2 / (nc - 1))
    welch = _t_quantile(0.975, df) * (vb + vc) ** 0.5
    result = boot(baseline=baseline, candidate=candidate, resamples=40_000)
    low, high = result.degradation  # type: ignore[attr-defined]
    assert (high - low) / 2 == pytest.approx(welch, rel=0.04)
