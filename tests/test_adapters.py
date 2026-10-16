from __future__ import annotations

from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any

import pytest

from physim_gate.adapters import base
from physim_gate.adapters.base import AdapterError, discover, get_adapter, parse_options
from physim_gate.adapters.synthetic import SyntheticSpec, generate
from physim_gate.schema import RunResult, SchemaError, TrialStatus, dump_json

SPEC: dict[str, Any] = {
    "run_id": "r",
    "scenario": {"id": "s"},
    "seed": 1,
    "trials": 50,
    "failure_probability": 0.1,
    "metrics": {
        "time": {"direction": "lower_is_better", "unit": "s", "mean": 5, "stdev": 1, "min": 0},
        "ok": {
            "distribution": "bernoulli",
            "direction": "higher_is_better",
            "p": 0.8,
            "failure_value": 0,
        },
    },
}


class GoodAdapter:
    name = "good"
    description = "a test adapter"

    def load(self, source: Path, options: Any) -> RunResult:
        raise NotImplementedError


class NotAnAdapter:
    pass


def fake_entry_points(monkeypatch: pytest.MonkeyPatch, *points: EntryPoint) -> None:
    monkeypatch.setattr(base, "_entry_points", lambda: list(points))


def test_builtins_are_always_discoverable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_entry_points(monkeypatch)
    assert {"json", "synthetic", "gymnasium"} <= set(discover())
    assert get_adapter("json").name == "json"


def test_installed_entry_points_include_builtins() -> None:
    found = discover()
    assert found["json"].error is None


def test_third_party_adapter_is_loaded_from_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_entry_points(
        monkeypatch,
        EntryPoint(
            name="good", value="tests.test_adapters:GoodAdapter", group=base.ENTRY_POINT_GROUP
        ),
    )
    assert discover()["good"].target == "tests.test_adapters:GoodAdapter"
    assert get_adapter("good").description == "a test adapter"


def test_name_conflict_with_builtin_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_entry_points(
        monkeypatch,
        EntryPoint(
            name="json", value="tests.test_adapters:GoodAdapter", group=base.ENTRY_POINT_GROUP
        ),
    )
    found = discover()
    assert found["json"].origin == "built-in"
    conflicts = [i for i in found.values() if i.error]
    assert len(conflicts) == 1
    assert "conflicts" in (conflicts[0].error or "")


@pytest.mark.parametrize(
    ("target", "fragment"),
    [
        ("tests.does_not_exist:Adapter", "failed to import"),
        ("tests.test_adapters:Missing", "failed to import"),
        ("tests.test_adapters:NotAnAdapter", "does not implement the Adapter protocol"),
        ("tests.test_adapters:SPEC", "is not callable"),
    ],
)
def test_broken_plugins_give_clear_errors(
    monkeypatch: pytest.MonkeyPatch, target: str, fragment: str
) -> None:
    fake_entry_points(
        monkeypatch, EntryPoint(name="broken", value=target, group=base.ENTRY_POINT_GROUP)
    )
    assert "broken" in discover()
    with pytest.raises(AdapterError, match=fragment):
        get_adapter("broken")
    # A broken plugin does not affect the others.
    assert get_adapter("json").name == "json"


def test_unknown_adapter() -> None:
    with pytest.raises(AdapterError, match=r"unknown adapter 'nope'; available: .*json"):
        get_adapter("nope")


def test_parse_options() -> None:
    assert parse_options(["a=1", "b = two=2"]) == {"a": "1", "b": "two=2"}
    with pytest.raises(AdapterError, match="key=value"):
        parse_options(["novalue"])


def test_json_adapter_rejects_options(tmp_path: Path) -> None:
    with pytest.raises(AdapterError, match="does not accept option"):
        get_adapter("json").load(tmp_path / "x.json", {"x": "1"})


def test_synthetic_generation_is_deterministic() -> None:
    spec = SyntheticSpec.model_validate(SPEC)
    first, second = generate(spec), generate(spec)
    assert dump_json(first) == dump_json(second)
    failed = [t for t in first.trials if t.status is TrialStatus.FAILED]
    assert failed
    assert all(t.metrics["time"] is None and t.metrics["ok"] == 0 for t in failed)
    assert {t.metrics["ok"] for t in first.trials} <= {0.0, 1.0}


def test_synthetic_condition_component_is_shared_across_runs() -> None:
    spec = {**SPEC, "failure_probability": 0.0}
    spec["metrics"] = {"time": {**SPEC["metrics"]["time"], "correlation": 1.0}}
    a = generate(SyntheticSpec.model_validate({**spec, "seed": 1}))
    b = generate(SyntheticSpec.model_validate({**spec, "seed": 2}))
    assert a.metric_values("time") == b.metric_values("time")
    spec["metrics"] = {"time": {**SPEC["metrics"]["time"], "correlation": 0.0}}
    c = generate(SyntheticSpec.model_validate({**spec, "seed": 1}))
    d = generate(SyntheticSpec.model_validate({**spec, "seed": 2}))
    assert c.metric_values("time") != d.metric_values("time")


def test_synthetic_adapter_options(tmp_path: Path) -> None:
    import yaml

    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump(SPEC))
    adapter = get_adapter("synthetic")
    assert len(adapter.load(path, {"trials": "7"}).trials) == 7
    with pytest.raises(AdapterError, match="integer"):
        adapter.load(path, {"seed": "x"})
    with pytest.raises(AdapterError, match="invalid option"):
        adapter.load(path, {"trials": "-1"})
    with pytest.raises(AdapterError, match="does not accept"):
        adapter.load(path, {"color": "red"})
    bad = tmp_path / "bad.yaml"
    bad.write_text("run_id: r\n")
    with pytest.raises(SchemaError, match="not a valid synthetic spec"):
        adapter.load(bad, {})
