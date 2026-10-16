"""Optional Gymnasium integration: roll out a policy over seeds and record a run result.

Requires the ``gymnasium`` extra (``pip install "physim-gate[gymnasium]"``). Only CPU
environments that need no extra system packages (for example the classic-control suite) are
tested. Gymnasium's own reproducibility caveats apply: seeding ``reset`` fixes the initial
state, not every source of non-determinism in every environment.

From Python::

    from physim_gate.adapters.gymnasium import run_gymnasium
    run = run_gymnasium("CartPole-v1", my_policy, seeds=range(30), run_id="cand-42")

From the CLI, point the ``gymnasium`` adapter at a YAML spec::

    env_id: CartPole-v1
    policy: my_package.policies:balance   # callable(observation) -> action, or "random"
    episodes: 30
"""

from __future__ import annotations

import importlib
import platform
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from physim_gate.adapters.base import AdapterError, reject_unknown_options
from physim_gate.provenance import capture_environment, capture_git_source, utc_now
from physim_gate.schema import (
    Direction,
    Domain,
    Failure,
    Identifier,
    MetricSpec,
    RunResult,
    RunStatus,
    SchemaError,
    Simulator,
    Trial,
    TrialStatus,
    format_validation_error,
)

Policy = Callable[[Any], Any]


def _require_gymnasium() -> Any:
    try:
        return importlib.import_module("gymnasium")
    except ImportError as exc:
        raise AdapterError(
            "the gymnasium adapter needs the optional dependency: "
            'pip install "physim-gate[gymnasium]"'
        ) from exc


def run_gymnasium(
    env_id: str,
    policy: Policy | None,
    *,
    seeds: Iterable[int],
    run_id: str,
    policy_id: str | None = None,
    max_steps: int | None = None,
    env_kwargs: Mapping[str, Any] | None = None,
    domain: Domain = Domain.GENERIC,
) -> RunResult:
    """Roll out ``policy`` once per seed and return a run result.

    ``policy`` maps an observation to an action. ``None`` samples uniformly from the action space,
    seeded per episode. Exceptions raised by the policy or the environment mark that trial as
    ``error`` instead of aborting the run.
    """
    gym = _require_gymnasium()
    env = gym.make(env_id, **dict(env_kwargs or {}))
    trials: list[Trial] = []
    seed_list = list(seeds)
    try:
        for seed in seed_list:
            trials.append(_episode(env, policy, seed, max_steps))
    finally:
        env.close()

    errors = sum(1 for t in trials if t.status is not TrialStatus.OK)
    return RunResult(
        run_id=run_id,
        created_at=utc_now(),
        domain=domain,
        simulator=Simulator(name="gymnasium", version=str(gym.__version__)),
        scenario=Identifier(id=env_id),
        policy=Identifier(id=policy_id or _policy_name(policy)),
        seeds=seed_list,
        source=capture_git_source(),
        environment=capture_environment(implementation=platform.python_implementation()),
        status=RunStatus.PARTIAL if errors else RunStatus.COMPLETED,
        metrics={
            "episode_return": MetricSpec(
                direction=Direction.HIGHER_IS_BETTER,
                description="Sum of rewards over the episode.",
            ),
            "episode_length": MetricSpec(
                direction=Direction.HIGHER_IS_BETTER,
                unit="steps",
                description="Steps until termination or truncation.",
            ),
            "terminated": MetricSpec(
                direction=Direction.LOWER_IS_BETTER,
                description="1 if the episode ended in a terminal state, 0 if truncated.",
            ),
        },
        trials=trials,
    )


def _episode(env: Any, policy: Policy | None, seed: int, max_steps: int | None) -> Trial:
    total = 0.0
    steps = 0
    terminated = truncated = False
    try:
        observation, _ = env.reset(seed=seed)
        env.action_space.seed(seed)
        while not (terminated or truncated):
            action = env.action_space.sample() if policy is None else policy(observation)
            observation, reward, terminated, truncated, _ = env.step(action)
            total += float(reward)
            steps += 1
            if max_steps is not None and steps >= max_steps:
                truncated = True
    except Exception as exc:
        return Trial(
            trial_id=f"seed-{seed}",
            seed=seed,
            status=TrialStatus.ERROR,
            failure=Failure(kind=type(exc).__name__, message=str(exc)),
            metrics={"episode_return": None, "episode_length": None, "terminated": None},
        )
    return Trial(
        trial_id=f"seed-{seed}",
        seed=seed,
        metrics={
            "episode_return": total,
            "episode_length": float(steps),
            "terminated": 1.0 if terminated else 0.0,
        },
    )


def _policy_name(policy: Policy | None) -> str:
    if policy is None:
        return "random"
    module = getattr(policy, "__module__", None) or "unknown"
    name = getattr(policy, "__qualname__", None) or type(policy).__name__
    return f"{module}:{name}"


class GymnasiumSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    env_id: str
    policy: str = "random"
    run_id: str
    policy_id: str | None = None
    episodes: Annotated[int, Field(ge=1, le=100_000)] = 30
    first_seed: int = 0
    max_steps: Annotated[int, Field(ge=1)] | None = None
    env_kwargs: dict[str, Any] = Field(default_factory=dict)
    domain: Domain = Domain.GENERIC


def load_policy(reference: str) -> Policy | None:
    """Resolve ``"random"`` or ``"module:attribute"`` to a policy callable."""
    if reference == "random":
        return None
    module_name, sep, attr = reference.partition(":")
    if not sep or not attr:
        raise AdapterError(f"policy {reference!r} must be 'random' or 'module:attribute'")
    try:
        obj: Any = importlib.import_module(module_name)
        for part in attr.split("."):
            obj = getattr(obj, part)
    except (ImportError, AttributeError) as exc:
        raise AdapterError(f"cannot load policy {reference!r}: {exc}") from exc
    if not callable(obj):
        raise AdapterError(f"policy {reference!r} is not callable")
    policy: Policy = obj
    return policy


class GymnasiumAdapter:
    name = "gymnasium"
    description = "Runs a policy in a Gymnasium environment over seeds (optional extra, CPU)."

    def load(self, source: Path, options: Mapping[str, str]) -> RunResult:
        reject_unknown_options(self.name, options, {"run_id", "policy", "episodes"})
        try:
            data = yaml.safe_load(source.read_text(encoding="utf-8"))
        except OSError as exc:
            raise SchemaError(f"{source}: cannot read file ({exc.strerror or exc})") from exc
        except yaml.YAMLError as exc:
            raise SchemaError(f"{source}: invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise SchemaError(f"{source}: expected a mapping at the top level")
        data = {**data, **options}
        try:
            spec = GymnasiumSpec.model_validate(data)
        except ValidationError as exc:
            raise SchemaError(
                f"{source}: not a valid gymnasium spec", format_validation_error(exc)
            ) from exc
        return run_gymnasium(
            spec.env_id,
            load_policy(spec.policy),
            seeds=range(spec.first_seed, spec.first_seed + spec.episodes),
            run_id=spec.run_id,
            policy_id=spec.policy_id or spec.policy,
            max_steps=spec.max_steps,
            env_kwargs=spec.env_kwargs,
            domain=spec.domain,
        )
