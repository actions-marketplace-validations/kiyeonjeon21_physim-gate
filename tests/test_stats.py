from __future__ import annotations

import pytest

from physim_gate.config import Statistic
from physim_gate.stats import bootstrap_degradation, mean, median, quantile, stdev


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
