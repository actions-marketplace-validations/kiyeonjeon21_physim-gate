from __future__ import annotations

from typing import Any

from physim_gate.validation import lint_run
from tests.conftest import make_run


def messages(run_kwargs: dict[str, Any], values: dict[str, list[float | None]]) -> list[str]:
    return [f"{i.location}: {i.message}" for i in lint_run(make_run(values, **run_kwargs))]


def test_clean_run_only_warns_about_provenance() -> None:
    found = messages({}, {"x": [1.0, 2.0]})
    assert found == ["source.commit: no source commit; the run is hard to reproduce"]


def test_missing_values_are_counted() -> None:
    found = messages({"source": {"commit": "abc"}}, {"x": [1.0, None, 2.0]})
    assert found == ["metrics.x: missing in 1 of 3 trials (1 null)"]


def test_never_reported_metric() -> None:
    found = messages({"source": {"commit": "abc"}}, {"x": [None, None]})
    assert found == ["metrics.x: declared but no trial reports a value"]


def test_failed_trials_without_details_and_all_failed() -> None:
    found = messages(
        {"source": {"commit": "abc"}, "statuses": ["failed", "error"]},
        {"x": [1.0, 2.0]},
    )
    assert "trials[0]: trial 't0' has status 'failed' but no failure details" in found
    assert "status: run is 'completed' but every trial failed" in found


def test_empty_run() -> None:
    found = messages({"source": {"commit": "abc"}}, {})
    assert found == ["trials: run contains no trials"]
