from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from physim_gate.config import GateConfig
from physim_gate.schema import RunResult


def make_run(
    values: Mapping[str, Sequence[float | None]],
    *,
    run_id: str = "run",
    directions: Mapping[str, str] | None = None,
    units: Mapping[str, str] | None = None,
    trial_ids: Sequence[str] | None = None,
    statuses: Sequence[str] | None = None,
    scenario: str = "scenario",
    **extra: Any,
) -> RunResult:
    """Build a run from per-metric value lists (all lists share trial order)."""
    directions = directions or {}
    units = units or {}
    n = len(next(iter(values.values()))) if values else 0
    ids = list(trial_ids) if trial_ids is not None else [f"t{i}" for i in range(n)]
    trials = []
    for i, trial_id in enumerate(ids):
        trial: dict[str, Any] = {
            "trial_id": trial_id,
            "metrics": {name: series[i] for name, series in values.items()},
        }
        if statuses is not None:
            trial["status"] = statuses[i]
        trials.append(trial)
    data: dict[str, Any] = {
        "run_id": run_id,
        "simulator": {"name": "unit-test", "version": "1"},
        "scenario": {"id": scenario},
        "metrics": {
            name: {
                "direction": directions.get(name, "higher_is_better"),
                "unit": units.get(name, ""),
            }
            for name in values
        },
        "trials": trials,
        **extra,
    }
    return RunResult.model_validate(data)


def make_config(metrics: Mapping[str, Mapping[str, Any]], **sections: Any) -> GateConfig:
    return GateConfig.model_validate({"version": 1, "metrics": dict(metrics), **sections})


@pytest.fixture
def no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "160")
