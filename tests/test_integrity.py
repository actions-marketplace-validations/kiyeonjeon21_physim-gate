"""Regression tests for comparison-integrity defects found in the v0.3.0 review.

Each test reproduces a way to get a silently wrong verdict or exit code through the CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from physim_gate.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _plain_output(no_color: None) -> None:
    """Stable, uncolored, wide output for assertions."""


def run_doc(run_id: str, values: dict[str, float | None], **extra: Any) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "simulator": {"name": "sim", "version": "1"},
        "scenario": {"id": "s"},
        "metrics": {"err": {"direction": "lower_is_better", "unit": "m"}},
        "trials": [{"trial_id": k, "metrics": {"err": v}} for k, v in values.items()],
        **extra,
    }


def write(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return path


GATE = "version: 1\nmetrics:\n  err:\n    max_absolute_degradation: 0.1\n"
VALUES = {f"t{i}": 1.0 + 0.01 * (i % 5) for i in range(30)}


def compare(tmp_path: Path, baseline: Any, candidate: Any, gate: str = GATE) -> Any:
    b = write(tmp_path / "b.json", baseline)
    c = write(tmp_path / "c.json", candidate)
    g = write(tmp_path / "gate.yaml", gate)
    return runner.invoke(app, ["compare", "-b", str(b), "-c", str(c), "-g", str(g)])


# P0-1: trials that one run reports and the other silently omits count as missing.


def test_candidate_that_omits_trials_is_not_a_pass(tmp_path: Path) -> None:
    kept = {k: v for i, (k, v) in enumerate(VALUES.items()) if i < 20}
    result = compare(tmp_path, run_doc("b", VALUES), run_doc("c", kept))
    assert result.exit_code == 3, result.output
    assert "INVALID" in result.output
    assert "10 of 30 trials missing" in result.output
    assert "trial ids reported only by the baseline" in result.output


def test_omitted_trials_count_like_null_values(tmp_path: Path) -> None:
    """Leaving trials out must not be better than reporting them as missing."""
    omitted = {k: v for i, (k, v) in enumerate(VALUES.items()) if i < 20}
    nulled = {k: (v if i < 20 else None) for i, (k, v) in enumerate(VALUES.items())}
    a = compare(tmp_path, run_doc("b", VALUES), run_doc("c", omitted))
    b = compare(tmp_path, run_doc("b", VALUES), run_doc("c", nulled))
    assert a.exit_code == b.exit_code == 3


def test_omitted_trials_are_blocked_in_paired_mode_and_for_failure_rate(tmp_path: Path) -> None:
    kept = {k: v for i, (k, v) in enumerate(VALUES.items()) if i < 20}
    gate = GATE + "defaults:\n  pairing: paired\n"
    assert compare(tmp_path, run_doc("b", VALUES), run_doc("c", kept), gate).exit_code == 3
    gate = "version: 1\nmetrics:\n  physim.failure_rate:\n    max_absolute_degradation: 0.5\n"
    result = compare(tmp_path, run_doc("b", VALUES), run_doc("c", kept), gate)
    assert result.exit_code == 3, result.output


def test_max_missing_fraction_allows_omitted_trials(tmp_path: Path) -> None:
    kept = {k: v for i, (k, v) in enumerate(VALUES.items()) if i < 27}
    gate = "version: 1\ndefaults:\n  max_missing_fraction: 0.1\n" + GATE.removeprefix(
        "version: 1\n"
    )
    result = compare(tmp_path, run_doc("b", VALUES), run_doc("c", kept), gate)
    assert result.exit_code == 0, result.output


def test_omitted_trials_count_as_failures_when_missing_values_are_tolerated(
    tmp_path: Path,
) -> None:
    """The documented safeguard: tolerate missing values but gate physim.failure_rate."""
    kept = {k: v for i, (k, v) in enumerate(VALUES.items()) if i < 20}
    gate = (
        "version: 1\ndefaults:\n  max_missing_fraction: 0.5\nmetrics:\n"
        "  err:\n    max_absolute_degradation: 0.1\n"
        "  physim.failure_rate:\n    max_absolute_degradation: 0.05\n"
    )
    b = write(tmp_path / "b.json", run_doc("b", VALUES))
    c = write(tmp_path / "c.json", run_doc("c", kept))
    g = write(tmp_path / "gate.yaml", gate)
    result = runner.invoke(app, ["compare", "-b", str(b), "-c", str(c), "-g", str(g), "-f", "json"])
    assert result.exit_code == 1, result.output
    rate = next(
        m for m in json.loads(result.stdout)["metrics"] if m["name"] == "physim.failure_rate"
    )
    assert rate["verdict"] == "FAIL"
    assert rate["numerical"]["candidate"] == pytest.approx(10 / 30)
    assert rate["pairing"]["mode"] == "paired"


def test_yaml_errors_name_the_file(tmp_path: Path) -> None:
    gate = GATE + "  err:\n    max_absolute_degradation: 5.0\n"
    result = compare(tmp_path, run_doc("b", VALUES), run_doc("c", VALUES), gate)
    assert f'in "{tmp_path / "gate.yaml"}", line 5' in result.output
    assert "<unicode string>" not in result.output


def test_disjoint_trial_ids_are_independent_samples_not_missing(tmp_path: Path) -> None:
    other = {f"u{i}": v for i, v in enumerate(VALUES.values())}
    result = compare(tmp_path, run_doc("b", VALUES), run_doc("c", other))
    assert result.exit_code == 0, result.output


# P0-2: duplicate keys are rejected instead of silently keeping the last one.


def test_duplicate_keys_in_gate_config_are_rejected(tmp_path: Path) -> None:
    gate = GATE + "  err:\n    max_absolute_degradation: 5.0\n"
    result = compare(tmp_path, run_doc("b", VALUES), run_doc("c", VALUES), gate)
    assert result.exit_code == 2, result.output
    assert "duplicate key 'err'" in result.output


def test_duplicate_keys_in_run_json_are_rejected(tmp_path: Path) -> None:
    text = json.dumps(run_doc("c", VALUES)).replace(
        '"metrics": {"err": {"direction": "lower_is_better", "unit": "m"}}',
        '"metrics": {"err": {"direction": "lower_is_better", "unit": "m"}, '
        '"err": {"direction": "higher_is_better", "unit": "m"}}',
    )
    path = write(tmp_path / "dup.json", text)
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 1, result.output
    assert "duplicate key 'err'" in result.output


def test_duplicate_scenarios_in_suite_are_rejected(tmp_path: Path) -> None:
    b = write(tmp_path / "b.json", run_doc("b", VALUES))
    c = write(tmp_path / "c.json", run_doc("c", {k: v + 1 for k, v in VALUES.items()}))
    write(tmp_path / "gate.yaml", GATE)
    suite = write(
        tmp_path / "suite.yaml",
        f"version: 1\ndefaults: {{config: gate.yaml}}\nscenarios:\n"
        f"  s: {{baseline: {b}, candidate: {c}}}\n"
        f"  s: {{baseline: {b}, candidate: {b}}}\n",
    )
    result = runner.invoke(app, ["suite", str(suite), "-o", str(tmp_path / "out")])
    assert result.exit_code == 2, result.output
    assert "duplicate key 's'" in result.output


def test_duplicate_keys_in_lerobot_output_are_rejected(tmp_path: Path) -> None:
    text = (
        '{"per_task": [{"task_group": "g", "task_id": 0, "metrics": '
        '{"successes": [true], "successes": [false], "sum_rewards": [1], "max_rewards": [1]}}]}'
    )
    path = write(tmp_path / "eval_info.json", text)
    result = runner.invoke(app, ["validate", "-a", "lerobot", str(path)])
    assert result.exit_code == 1, result.output
    assert "duplicate key 'successes'" in result.output


# P0-3: an adapter that calls sys.exit() must not turn into the process's exit code.


class ExitingAdapter:
    name = "exiting"
    description = "calls sys.exit while loading"

    def load(self, source: Path, options: Any) -> Any:
        raise SystemExit(0)


def test_adapter_calling_sys_exit_is_an_error_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from physim_gate.adapters import base

    monkeypatch.setitem(base.BUILTIN_ADAPTERS, "exiting", "tests.test_integrity:ExitingAdapter")
    b = write(tmp_path / "b.json", run_doc("b", VALUES))
    g = write(tmp_path / "gate.yaml", GATE)
    out = tmp_path / "comparison.json"
    result = runner.invoke(
        app, ["compare", "-a", "exiting", "-b", str(b), "-c", str(b), "-g", str(g), "-o", str(out)]
    )
    assert result.exit_code == 2, result.output
    assert "adapter 'exiting' called sys.exit(0)" in result.output
    assert not out.exists()
    validated = runner.invoke(app, ["validate", "-a", "exiting", str(b)])
    assert validated.exit_code == 2, validated.output
