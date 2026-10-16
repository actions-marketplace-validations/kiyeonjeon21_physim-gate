from __future__ import annotations

import itertools
import json
import random
from math import comb

import pytest
from typer.testing import CliRunner

from physim_gate.cli import app
from physim_gate.config import GateConfig
from physim_gate.engine import compare_runs
from physim_gate.planning import (
    Goal,
    PlanningError,
    binary_verdict_probabilities,
    plan_binary,
    plan_continuous,
)
from physim_gate.schema import Direction
from physim_gate.stats import newcombe_counts
from tests.conftest import make_run


def brute_force(n: int, qb: float, qc: float, margin: float) -> tuple[float, float]:
    p_pass = p_fail = 0.0
    for xb, xc in itertools.product(range(n + 1), repeat=2):
        weight = (
            comb(n, xb)
            * qb**xb
            * (1 - qb) ** (n - xb)
            * comb(n, xc)
            * qc**xc
            * (1 - qc) ** (n - xc)
        )
        low, high = newcombe_counts(xb, n, xc, n, 0.95)
        if high <= margin:
            p_pass += weight
        elif low > margin:
            p_fail += weight
    return p_pass, p_fail


@pytest.mark.parametrize("n", [10, 23])
def test_exact_probabilities_match_brute_force(n: int) -> None:
    for qb, qc, margin in itertools.product(
        [0.0, 0.03, 0.2, 0.5, 0.9], [0.0, 0.1, 0.45, 1.0], [0.0, 0.05, 0.2]
    ):
        result = binary_verdict_probabilities(n, qb, qc, margin, 0.95)
        expected = brute_force(n, qb, qc, margin)
        assert result.pass_ == pytest.approx(expected[0], abs=1e-12)
        assert result.fail == pytest.approx(expected[1], abs=1e-12)
        assert result.pass_ + result.fail + result.inconclusive == pytest.approx(1.0)


def test_binary_plan_matches_the_engine() -> None:
    """At the planned size, the real engine PASSes about as often as the plan says."""
    plan = plan_binary(0.7, 0.15)
    assert plan.goal is Goal.PASS
    assert plan.trials is not None
    assert plan.probabilities is not None
    n = plan.trials
    config = GateConfig.model_validate(
        {
            "version": 1,
            "defaults": {"pairing": "unpaired"},
            "metrics": {"success": {"max_absolute_degradation": 0.15}},
        }
    )
    rng = random.Random(7)
    reps = 400
    passes = 0
    for _ in range(reps):
        runs = [
            make_run(
                {"success": [1.0 if rng.random() < 0.7 else 0.0 for _ in range(n)]},
                run_id=name,
                directions={"success": "higher_is_better"},
            )
            for name in ("base", "cand")
        ]
        result = compare_runs(runs[0], runs[1], config)
        passes += result.metrics[0].verdict.value == "PASS"
    # 400 repetitions: the standard error of the observed rate is about 0.02.
    assert passes / reps == pytest.approx(plan.probabilities.pass_, abs=0.07)


def test_binary_plans() -> None:
    plan = plan_binary(0.9, 0.05)
    assert plan.trials == 573
    assert plan.probabilities is not None
    assert plan.probabilities.pass_ >= 0.8
    smaller = binary_verdict_probabilities(572, 0.1, 0.1, 0.05, 0.95)
    assert smaller.pass_ < 0.8

    regression = plan_binary(0.02, 0.01, direction=Direction.LOWER_IS_BETTER, true_degradation=0.03)
    assert regression.goal is Goal.FAIL
    assert regression.probabilities is not None
    assert regression.probabilities.fail >= 0.8

    # Higher-is-better and lower-is-better describe the same question from both sides.
    mirrored = plan_binary(0.1, 0.05, direction=Direction.LOWER_IS_BETTER)
    assert mirrored.trials == plan.trials

    assert plan_binary(0.5, 0.6, min_samples=10).trials == 10
    assert plan_binary(0.5, 0.001, max_trials=1000).trials is None


def test_continuous_plans() -> None:
    unpaired = plan_continuous(2.0, 0.5)
    paired = plan_continuous(2.0, 0.5, paired=True)
    assert unpaired.trials == 252
    assert paired.trials == 126
    assert unpaired.probabilities is not None
    assert unpaired.probabilities.pass_ == pytest.approx(0.8, abs=0.01)
    regression = plan_continuous(1.0, 0.1, true_degradation=0.6)
    assert regression.goal is Goal.FAIL
    assert regression.trials == 63
    assert plan_continuous(1.0, 0.0001, max_trials=1000).trials is None
    assert any("below 30 trials" in note for note in plan_continuous(1.0, 2.0).notes)


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: plan_binary(0.9, 0.05, true_degradation=0.05), "equals the margin"),
        (lambda: plan_binary(0.9, -0.1), "margin must be >= 0"),
        (lambda: plan_binary(1.2, 0.1), "rate must be between 0 and 1"),
        (lambda: plan_binary(0.05, 0.1, true_degradation=0.2), "leaves"),
        (lambda: plan_binary(0.5, 0.1, confidence=0.4), "confidence"),
        (lambda: plan_binary(0.5, 0.1, power=1.0), "power"),
        (lambda: plan_continuous(0.0, 0.1), "stdev must be > 0"),
    ],
)
def test_planning_errors(call: object, message: str) -> None:
    with pytest.raises(PlanningError, match=message):
        call()  # type: ignore[operator]


def test_plan_cli() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["plan", "--rate", "0.9", "--margin", "0.05"])
    assert result.exit_code == 0, result.output
    assert "573 trials per run" in result.output
    assert "PASS 80.1%" in result.output

    result = runner.invoke(app, ["plan", "-s", "2", "-m", "0.5", "--paired", "-f", "json"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["trials"] == 126
    assert document["goal"] == "pass"
    assert set(document["probabilities"]) == {"pass", "inconclusive", "fail"}

    result = runner.invoke(app, ["plan", "-r", "0.5", "-m", "0.001", "--min-samples", "10"])
    assert result.exit_code == 0, result.output

    for args, message in [
        (["plan", "-m", "0.1"], "exactly one of --rate"),
        (["plan", "-m", "0.1", "-r", "0.5", "-s", "1"], "exactly one of --rate"),
        (["plan", "-m", "0.1", "-r", "0.5", "--paired"], "--paired applies to --stdev"),
        (["plan", "-m", "0.1", "-r", "0.5", "-t", "0.1"], "equals the margin"),
    ]:
        result = runner.invoke(app, args)
        assert result.exit_code == 2
        assert message in result.output
