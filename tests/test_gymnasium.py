from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("gymnasium")

from physim_gate.adapters.base import AdapterError, get_adapter
from physim_gate.adapters.gymnasium import load_policy, run_gymnasium
from physim_gate.schema import RunStatus, TrialStatus, dump_json


def lean_policy(observation: Any) -> int:
    """Push the cart toward the side the pole is falling to."""
    return 1 if observation[2] > 0 else 0


def exploding_policy(observation: Any) -> int:
    raise RuntimeError("controller diverged")


def test_runs_episodes_over_seeds() -> None:
    run = run_gymnasium("CartPole-v1", lean_policy, seeds=range(5), run_id="cp")
    assert [t.trial_id for t in run.trials] == [f"seed-{i}" for i in range(5)]
    assert run.simulator.name == "gymnasium"
    assert run.policy is not None
    assert run.policy.id.endswith("lean_policy")
    assert all(t.metrics["episode_length"] == t.metrics["episode_return"] for t in run.trials)


def test_same_seeds_give_same_results_for_deterministic_env() -> None:
    a = run_gymnasium("CartPole-v1", lean_policy, seeds=range(3), run_id="a")
    b = run_gymnasium("CartPole-v1", lean_policy, seeds=range(3), run_id="a")
    assert [t.metrics for t in a.trials] == [t.metrics for t in b.trials]


def test_random_policy_and_max_steps() -> None:
    run = run_gymnasium("CartPole-v1", None, seeds=[1, 2], run_id="r", max_steps=3)
    assert run.policy is not None
    assert run.policy.id == "random"
    assert all((t.metrics["episode_length"] or 0) <= 3 for t in run.trials)


def test_policy_errors_mark_trials_instead_of_crashing() -> None:
    run = run_gymnasium("CartPole-v1", exploding_policy, seeds=[0, 1], run_id="x")
    assert run.status is RunStatus.PARTIAL
    assert all(t.status is TrialStatus.ERROR for t in run.trials)
    assert run.trials[0].failure is not None
    assert run.trials[0].failure.message == "controller diverged"
    assert run.trials[0].metrics["episode_return"] is None
    dump_json(run)


def test_adapter_reads_spec(tmp_path: Path) -> None:
    spec = tmp_path / "gym.yaml"
    spec.write_text(
        "env_id: CartPole-v1\npolicy: tests.test_gymnasium:lean_policy\nrun_id: c\nepisodes: 4\n"
    )
    run = get_adapter("gymnasium").load(spec, {"episodes": "2"})
    assert len(run.trials) == 2


def test_policy_reference_errors() -> None:
    assert load_policy("random") is None
    with pytest.raises(AdapterError, match="module:attribute"):
        load_policy("no_colon")
    with pytest.raises(AdapterError, match="cannot load policy"):
        load_policy("tests.test_gymnasium:missing")
