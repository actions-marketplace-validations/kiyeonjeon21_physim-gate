"""Deterministic synthetic run generator for tests, demos and documentation.

It does not simulate physics. It draws per-trial metric values from simple distributions so the
comparison engine can be exercised end to end. Each trial's value mixes two sources of variation:

* a *condition* component shared by every run that uses the same ``trial_id`` (for example the
  same initial state), which is what makes paired comparison informative, and
* a *noise* component that differs between runs, standing in for non-determinism such as
  physics-step ordering or GPU reductions.

``correlation`` sets the share of variance that comes from the condition component.
Generation is fully determined by the spec, so the same spec always yields byte-identical JSON.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, ValidationError

from physim_gate.adapters.base import AdapterError, reject_unknown_options
from physim_gate.schema import (
    Direction,
    Domain,
    Environment,
    Failure,
    Identifier,
    InputError,
    MetricSpec,
    RunResult,
    SchemaError,
    Simulator,
    Source,
    Trial,
    TrialStatus,
    format_validation_error,
    load_yaml,
)

GENERATOR_NAME = "physim-gate-synthetic"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


Correlation = Annotated[float, Field(ge=0.0, le=1.0)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class NormalMetric(_Model):
    distribution: Literal["normal"] = "normal"
    direction: Direction
    unit: str = ""
    description: str = ""
    mean: float
    stdev: Annotated[float, Field(ge=0)]
    min: float | None = None
    max: float | None = None
    round_to: Annotated[int, Field(ge=0, le=12)] = 6
    correlation: Correlation = 0.5
    missing_probability: Probability = 0.0
    failure_value: float | None = None


class BernoulliMetric(_Model):
    distribution: Literal["bernoulli"]
    direction: Direction
    unit: str = ""
    description: str = ""
    p: Probability
    correlation: Correlation = 0.5
    missing_probability: Probability = 0.0
    failure_value: float | None = None


def _distribution_tag(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("distribution", "normal"))
    return str(getattr(value, "distribution", "normal"))


SyntheticMetric = Annotated[
    Annotated[NormalMetric, Tag("normal")] | Annotated[BernoulliMetric, Tag("bernoulli")],
    Discriminator(_distribution_tag),
]


class SyntheticSpec(_Model):
    run_id: str
    domain: Domain = Domain.GENERIC
    simulator: Simulator = Simulator(name=GENERATOR_NAME, version="1")
    scenario: Identifier
    policy: Identifier | None = None
    source: Source = Field(default_factory=Source)
    created_at: datetime | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    seed: int
    trials: Annotated[int, Field(ge=0, le=100_000)]
    first_trial_seed: int = 0
    failure_probability: Probability = 0.0
    failure_kind: str = "synthetic_failure"
    metrics: dict[str, SyntheticMetric]


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def generate(spec: SyntheticSpec) -> RunResult:
    """Generate a run result from a spec. Pure and deterministic."""
    noise = random.Random(f"noise:{spec.seed}")
    trials: list[Trial] = []
    seeds: list[int] = []
    for index in range(spec.trials):
        trial_seed = spec.first_trial_seed + index
        seeds.append(trial_seed)
        failed = noise.random() < spec.failure_probability
        values: dict[str, float | None] = {}
        for name, metric in spec.metrics.items():
            condition = random.Random(f"condition:{spec.scenario.id}:{name}:{trial_seed}")
            z = (
                math.sqrt(metric.correlation) * condition.gauss()
                + math.sqrt(1.0 - metric.correlation) * noise.gauss()
            )
            missing = noise.random() < metric.missing_probability
            if failed:
                values[name] = metric.failure_value
            elif missing:
                values[name] = None
            elif isinstance(metric, BernoulliMetric):
                values[name] = 1.0 if _phi(z) < metric.p else 0.0
            else:
                value = metric.mean + metric.stdev * z
                if metric.min is not None:
                    value = max(metric.min, value)
                if metric.max is not None:
                    value = min(metric.max, value)
                values[name] = round(value, metric.round_to)
        trials.append(
            Trial(
                trial_id=f"seed-{trial_seed}",
                seed=trial_seed,
                status=TrialStatus.FAILED if failed else TrialStatus.OK,
                failure=Failure(kind=spec.failure_kind, message="injected by the synthetic spec")
                if failed
                else None,
                metrics=values,
            )
        )
    return RunResult(
        run_id=spec.run_id,
        created_at=spec.created_at,
        domain=spec.domain,
        simulator=spec.simulator,
        scenario=spec.scenario,
        policy=spec.policy,
        seeds=seeds,
        source=spec.source,
        environment=Environment(extra={"generator": GENERATOR_NAME, "generator_seed": spec.seed}),
        metrics={
            name: MetricSpec(direction=m.direction, unit=m.unit, description=m.description)
            for name, m in spec.metrics.items()
        },
        trials=trials,
        labels=spec.labels,
    )


def load_spec(path: Path) -> SyntheticSpec:
    try:
        data = load_yaml(path.read_text(encoding="utf-8"), str(path))
    except OSError as exc:
        raise InputError(f"{path}: cannot read file ({exc.strerror or exc})") from exc
    except yaml.YAMLError as exc:
        raise SchemaError(f"{path}: invalid YAML: {exc}") from exc
    try:
        return SyntheticSpec.model_validate(data)
    except ValidationError as exc:
        raise SchemaError(
            f"{path}: not a valid synthetic spec", format_validation_error(exc)
        ) from exc


class SyntheticAdapter:
    name = "synthetic"
    description = "Generates a deterministic synthetic run from a YAML spec (tests and demos)."

    def load(self, source: Path, options: Mapping[str, str]) -> RunResult:
        reject_unknown_options(self.name, options, {"seed", "trials"})
        spec = load_spec(source)
        overrides: dict[str, int] = {}
        for key in ("seed", "trials"):
            if key in options:
                try:
                    overrides[key] = int(options[key])
                except ValueError as exc:
                    raise AdapterError(f"option {key} must be an integer") from exc
        if overrides:
            try:
                spec = SyntheticSpec.model_validate({**spec.model_dump(), **overrides})
            except ValidationError as exc:
                raise AdapterError(
                    "invalid option: " + "; ".join(format_validation_error(exc))
                ) from exc
        return generate(spec)
