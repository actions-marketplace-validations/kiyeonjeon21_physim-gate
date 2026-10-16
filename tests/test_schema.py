from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from physim_gate.schema import (
    FAILURE_RATE_METRIC,
    InputError,
    RunResult,
    SchemaError,
    dump_json,
    load_run,
    run_from_data,
)


def minimal(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": "r1",
        "simulator": {"name": "sim"},
        "scenario": {"id": "s"},
        "metrics": {"success": {"direction": "higher_is_better"}},
        "trials": [
            {"trial_id": "a", "metrics": {"success": 1}},
            {"trial_id": "b", "metrics": {"success": 0.0}},
        ],
    }
    data.update(overrides)
    return data


def test_minimal_document_is_valid() -> None:
    run = run_from_data(minimal())
    assert run.domain.value == "generic"
    assert run.metric_values("success") == {"a": 1.0, "b": 0.0}


def test_zero_is_a_value_and_null_or_absent_is_missing() -> None:
    data = minimal(
        metrics={
            "x": {"direction": "lower_is_better"},
            "y": {"direction": "lower_is_better"},
        },
        trials=[
            {"trial_id": "a", "metrics": {"x": 0, "y": None}},
            {"trial_id": "b", "metrics": {"x": 0.0}},
        ],
    )
    run = run_from_data(data)
    assert run.metric_values("x") == {"a": 0.0, "b": 0.0}
    assert run.metric_values("y") == {"a": None, "b": None}


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"schema_version": "2.0"}, "unsupported major version"),
        ({"schema_version": "one"}, "must look like"),
        ({"domain": "underwater"}, "domain"),
        ({"metrics": {"success": {"direction": "up"}}}, "direction"),
        ({"metrics": {"physim.x": {"direction": "higher_is_better"}}}, "reserved prefix"),
        ({"metrics": {"bad name": {"direction": "higher_is_better"}}}, "invalid metric name"),
        ({"unknown_field": 1}, "unknown_field"),
        ({"run_id": ""}, "run_id"),
    ],
)
def test_invalid_documents(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(SchemaError) as info:
        run_from_data(minimal(**overrides))
    assert fragment in str(info.value)


def test_duplicate_trial_ids_rejected() -> None:
    data = minimal(trials=[{"trial_id": "a"}, {"trial_id": "a"}])
    with pytest.raises(SchemaError, match="duplicate trial_id 'a'"):
        run_from_data(data)


def test_undeclared_metric_rejected() -> None:
    data = minimal(trials=[{"trial_id": "a", "metrics": {"speed": 1.0}}])
    with pytest.raises(SchemaError, match="undeclared metric"):
        run_from_data(data)


@pytest.mark.parametrize("value", ["1.0", True, [1], {"v": 1}])
def test_malformed_metric_values_rejected(value: Any) -> None:
    data = minimal(trials=[{"trial_id": "a", "metrics": {"success": value}}])
    with pytest.raises(SchemaError):
        run_from_data(data)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_rejected_from_python(value: float) -> None:
    data = minimal(trials=[{"trial_id": "a", "metrics": {"success": value}}])
    with pytest.raises(SchemaError):
        run_from_data(data)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_tokens_rejected_from_json(tmp_path: Path, token: str) -> None:
    text = json.dumps(minimal()).replace('"success": 1}', f'"success": {token}}}')
    path = tmp_path / "run.json"
    path.write_text(text)
    with pytest.raises(SchemaError, match="non-finite"):
        load_run(path)


def test_invalid_json_and_missing_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(SchemaError, match="invalid JSON at line 1"):
        load_run(broken)
    with pytest.raises(SchemaError, match="cannot read file"):
        load_run(tmp_path / "missing.json")
    top_level_list = tmp_path / "list.json"
    top_level_list.write_text("[]")
    with pytest.raises(SchemaError, match="JSON object"):
        load_run(top_level_list)


def test_comparison_document_is_not_a_run() -> None:
    with pytest.raises(SchemaError, match="comparison result"):
        run_from_data({"kind": "physim-gate.comparison"})


def test_minor_versions_are_accepted() -> None:
    assert run_from_data(minimal(schema_version="1.7")).schema_version == "1.7"


def test_round_trip_is_byte_stable(tmp_path: Path) -> None:
    run = run_from_data(minimal(created_at="2026-01-02T03:04:05Z", labels={"ci": "yes"}))
    text = dump_json(run)
    assert text.endswith("\n")
    path = tmp_path / "run.json"
    path.write_text(text)
    again = load_run(path)
    assert again == run
    assert dump_json(again) == text


def test_failure_rate_is_derived_from_status() -> None:
    data = minimal(
        trials=[
            {"trial_id": "a", "status": "ok", "metrics": {"success": 1}},
            {"trial_id": "b", "status": "timeout", "failure": {"kind": "timeout"}},
            {"trial_id": "c", "status": "error", "failure": {"kind": "crash"}},
        ]
    )
    run = run_from_data(data)
    assert run.metric_values(FAILURE_RATE_METRIC) == {"a": 0.0, "b": 1.0, "c": 1.0}
    spec = run.metric_spec(FAILURE_RATE_METRIC)
    assert spec is not None
    assert spec.direction.value == "lower_is_better"


def test_published_json_schema_is_current() -> None:
    schemas = Path(__file__).parent.parent / "schemas"
    published = json.loads((schemas / "run-result.schema.json").read_text())
    assert published == RunResult.model_json_schema()


def test_integers_too_large_for_a_float_are_rejected(tmp_path: Path) -> None:
    text = json.dumps(minimal()).replace('"success": 1}', '"success": ' + "9" * 400 + "}")
    path = tmp_path / "run.json"
    path.write_text(text)
    with pytest.raises(SchemaError, match="too large"):
        load_run(path)


def test_unreadable_input_is_an_input_error(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="cannot read file"):
        load_run(tmp_path)
