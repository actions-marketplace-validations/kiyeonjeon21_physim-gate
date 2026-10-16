"""Versioned, simulator-independent schema for simulation run results.

A :class:`RunResult` describes one *run*: a batch of trials (episodes) of one policy on one
scenario in one simulator, together with the provenance needed to reproduce it.

Missing data is explicit. Inside ``Trial.metrics`` an absent key means "not reported" and an
explicit ``null`` means "reported as unavailable". Both count as missing for comparison
purposes, while ``0.0`` is always a valid observation. Non-finite numbers are rejected.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictFloat,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

RUN_SCHEMA_MAJOR = 1
RUN_SCHEMA_VERSION = "1.0"

#: Metric names produced by PhySim Gate itself use this prefix; runs may not declare them.
RESERVED_METRIC_PREFIX = "physim."
#: Derived per-trial indicator: 1.0 when the trial status is not ``ok``, else 0.0.
FAILURE_RATE_METRIC = "physim.failure_rate"

_METRIC_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:/\-]*$")
_SCHEMA_VERSION = re.compile(r"^(\d+)\.(\d+)$")

MetricValue = Annotated[StrictFloat | StrictInt, Field(allow_inf_nan=False)]
NonEmptyStr = Annotated[str, Field(min_length=1)]


class SchemaError(ValueError):
    """Raised when a document cannot be parsed as a valid result document.

    ``problems`` holds one human-readable line per problem, each prefixed with its location.
    """

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.problems = problems or []

    def __str__(self) -> str:
        if not self.problems:
            return self.message
        return self.message + "\n" + "\n".join(f"  - {p}" for p in self.problems)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class Domain(StrEnum):
    ROBOTICS = "robotics"
    AUTONOMOUS_DRIVING = "autonomous_driving"
    AERIAL = "aerial"
    GENERIC = "generic"


class Direction(StrEnum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


class TrialStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ERROR = "error"


class RunStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class Simulator(_Model):
    name: NonEmptyStr
    version: str | None = None


class Identifier(_Model):
    """Identifies a scenario or a policy, optionally pinned to a version or checkpoint."""

    id: NonEmptyStr
    version: str | None = None


class Source(_Model):
    """Where the code under test came from."""

    repository: str | None = None
    commit: str | None = None
    branch: str | None = None
    dirty: bool | None = None


class Environment(_Model):
    """The execution environment the run was produced in."""

    python: str | None = None
    platform: str | None = None
    machine: str | None = None
    extra: dict[str, JsonValue] = Field(default_factory=dict)


class Failure(_Model):
    kind: NonEmptyStr
    message: str = ""


class MetricSpec(_Model):
    """Declares a metric: its unit and which direction is an improvement."""

    direction: Direction
    unit: str = ""
    description: str = ""


class ArtifactRef(_Model):
    """A pointer to a file produced by the run (video, log, trace, rosbag, ...)."""

    name: NonEmptyStr
    uri: NonEmptyStr
    media_type: str | None = None
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None


class Trial(_Model):
    """One episode or trial.

    ``trial_id`` names the trial *condition* (for example scenario variant and seed). Trials with
    the same id in two runs are treated as matched pairs. Matching ids only mean the conditions
    were intended to be the same. They do not imply bitwise-identical physics.
    """

    trial_id: NonEmptyStr
    seed: int | None = None
    status: TrialStatus = TrialStatus.OK
    failure: Failure | None = None
    metrics: dict[str, MetricValue | None] = Field(default_factory=dict)
    duration_s: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)


class RunResult(_Model):
    """A complete, self-describing record of one simulation run."""

    schema_version: str = RUN_SCHEMA_VERSION
    run_id: NonEmptyStr
    created_at: datetime | None = None
    domain: Domain = Domain.GENERIC
    simulator: Simulator
    scenario: Identifier
    policy: Identifier | None = None
    seeds: list[int] = Field(default_factory=list)
    source: Source = Field(default_factory=Source)
    environment: Environment = Field(default_factory=Environment)
    status: RunStatus = RunStatus.COMPLETED
    failure: Failure | None = None
    metrics: dict[str, MetricSpec]
    trials: list[Trial]
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: str) -> str:
        match = _SCHEMA_VERSION.match(value)
        if match is None:
            raise ValueError(f"must look like '{RUN_SCHEMA_MAJOR}.<minor>', got {value!r}")
        if int(match.group(1)) != RUN_SCHEMA_MAJOR:
            raise ValueError(
                f"unsupported major version {value!r}; this release reads "
                f"{RUN_SCHEMA_MAJOR}.x run results"
            )
        return value

    @field_validator("metrics")
    @classmethod
    def _check_metric_names(cls, value: dict[str, MetricSpec]) -> dict[str, MetricSpec]:
        for name in value:
            if not _METRIC_NAME.match(name):
                raise ValueError(f"invalid metric name {name!r}; use letters, digits and _ . : / -")
            if name.startswith(RESERVED_METRIC_PREFIX):
                raise ValueError(
                    f"metric name {name!r} uses the reserved prefix {RESERVED_METRIC_PREFIX!r}"
                )
        return value

    @model_validator(mode="after")
    def _check_trials(self) -> Self:
        seen: set[str] = set()
        for index, trial in enumerate(self.trials):
            if trial.trial_id in seen:
                raise ValueError(f"trials[{index}]: duplicate trial_id {trial.trial_id!r}")
            seen.add(trial.trial_id)
            undeclared = sorted(set(trial.metrics) - set(self.metrics))
            if undeclared:
                raise ValueError(
                    f"trials[{index}] ({trial.trial_id!r}) reports undeclared metric(s) "
                    f"{', '.join(undeclared)}; declare them under 'metrics'"
                )
        return self

    def metric_values(self, name: str) -> dict[str, float | None]:
        """Per-trial values of a metric keyed by ``trial_id`` (``None`` = missing).

        The derived :data:`FAILURE_RATE_METRIC` is computed from each trial's status.
        """
        if name == FAILURE_RATE_METRIC:
            return {t.trial_id: 0.0 if t.status is TrialStatus.OK else 1.0 for t in self.trials}
        values: dict[str, float | None] = {}
        for trial in self.trials:
            raw = trial.metrics.get(name)
            values[trial.trial_id] = None if raw is None else float(raw)
        return values

    def metric_spec(self, name: str) -> MetricSpec | None:
        if name == FAILURE_RATE_METRIC:
            return FAILURE_RATE_SPEC
        return self.metrics.get(name)


FAILURE_RATE_SPEC = MetricSpec(
    direction=Direction.LOWER_IS_BETTER,
    unit="fraction",
    description="Fraction of trials whose status is not 'ok' (derived by PhySim Gate).",
)


def format_validation_error(error: ValidationError) -> list[str]:
    """Turn a pydantic error into ``location: message`` lines."""
    problems = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or "<root>"
        message = item["msg"].removeprefix("Value error, ")
        problems.append(f"{location}: {message}")
    return problems


def parse_json_text(text: str, origin: str) -> Any:
    """Parse JSON, rejecting the non-standard ``NaN``/``Infinity`` tokens Python accepts."""

    def _reject_constant(token: str) -> Any:
        raise SchemaError(f"{origin}: non-finite number {token!r} is not valid JSON")

    try:
        return json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"{origin}: invalid JSON at line {exc.lineno}: {exc.msg}") from exc


def run_from_data(data: Any, origin: str = "<data>") -> RunResult:
    if not isinstance(data, dict):
        raise SchemaError(f"{origin}: expected a JSON object at the top level")
    if data.get("kind") == "physim-gate.comparison":
        raise SchemaError(f"{origin}: this is a comparison result, not a run result")
    try:
        return RunResult.model_validate(data)
    except ValidationError as exc:
        raise SchemaError(
            f"{origin}: not a valid run result", format_validation_error(exc)
        ) from exc


def load_run(path: Path) -> RunResult:
    """Read and validate a run result JSON file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaError(f"{path}: cannot read file ({exc.strerror or exc})") from exc
    return run_from_data(parse_json_text(text, str(path)), str(path))


def dump_json(model: BaseModel) -> str:
    """Serialize a model deterministically: stable field order, 2-space indent, final newline."""
    return model.model_dump_json(indent=2) + "\n"


def write_run(run: RunResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_json(run), encoding="utf-8")
