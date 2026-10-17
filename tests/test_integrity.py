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


# P1: run-level comparability checks found in the v0.3.0 review.


def json_compare(tmp_path: Path, baseline: Any, candidate: Any, gate: str = GATE) -> Any:
    b = write(tmp_path / "b.json", baseline)
    c = write(tmp_path / "c.json", candidate)
    g = write(tmp_path / "gate.yaml", gate)
    result = runner.invoke(app, ["compare", "-b", str(b), "-c", str(c), "-g", str(g), "-f", "json"])
    assert result.exit_code in (0, 1, 3), result.output
    return result.exit_code, json.loads(result.stdout)


def with_seeds(doc: dict[str, Any], offset: int) -> dict[str, Any]:
    for i, trial in enumerate(doc["trials"]):
        trial["seed"] = i + offset
    return doc


def test_shared_trial_ids_with_different_seeds_are_not_comparable(tmp_path: Path) -> None:
    code, result = json_compare(
        tmp_path, with_seeds(run_doc("b", VALUES), 0), with_seeds(run_doc("c", VALUES), 100)
    )
    assert code == 3
    assert result["verdict"] == "INVALID"
    assert any("30 trial ids have different seeds" in i for i in result["run_blocking_issues"])
    assert any("t0: 0 vs 100" in i for i in result["run_blocking_issues"])

    same, _ = json_compare(
        tmp_path, with_seeds(run_doc("b", VALUES), 0), with_seeds(run_doc("c", VALUES), 0)
    )
    assert same == 0
    one_sided, _ = json_compare(tmp_path, with_seeds(run_doc("b", VALUES), 0), run_doc("c", VALUES))
    assert one_sided == 0
    unpaired = GATE + "    pairing: unpaired\n"
    code, result = json_compare(
        tmp_path,
        with_seeds(run_doc("b", VALUES), 0),
        with_seeds(run_doc("c", VALUES), 100),
        unpaired,
    )
    assert code == 0
    assert any("different seeds" in i for i in result["run_issues"])


def test_a_failed_run_is_not_comparable(tmp_path: Path) -> None:
    code, result = json_compare(
        tmp_path, run_doc("b", VALUES), run_doc("c", VALUES, status="failed")
    )
    assert code == 3
    assert result["run_blocking_issues"] == ["candidate run status is 'failed'"]
    code, result = json_compare(
        tmp_path, run_doc("b", VALUES), run_doc("c", VALUES, status="partial")
    )
    assert code == 0
    assert "candidate run status is 'partial'" in result["run_issues"]


def test_a_run_in_which_every_trial_failed_is_not_comparable(tmp_path: Path) -> None:
    candidate = run_doc("c", VALUES)
    for trial in candidate["trials"]:
        trial["status"] = "timeout"
    code, result = json_compare(tmp_path, run_doc("b", VALUES), candidate)
    assert code == 3
    assert result["run_blocking_issues"] == [
        "every candidate trial has a status other than 'ok' (30 of 30)"
    ]


def test_failed_trials_are_pointed_out_when_the_failure_rate_is_not_gated(tmp_path: Path) -> None:
    candidate = run_doc("c", VALUES)
    candidate["trials"][0]["status"] = "error"
    code, result = json_compare(tmp_path, run_doc("b", VALUES), candidate)
    assert code == 0
    assert (
        "1 of 30 candidate trials have a status other than 'ok'; gate physim.failure_rate to "
        "catch more failures" in result["run_issues"]
    )


def test_a_changed_scenario_version_is_not_comparable(tmp_path: Path) -> None:
    v1 = run_doc("b", VALUES, scenario={"id": "s", "version": "1"})
    v2 = run_doc("c", VALUES, scenario={"id": "s", "version": "2"})
    code, result = json_compare(tmp_path, v1, v2)
    assert code == 3
    assert any("scenario version differs" in i for i in result["run_blocking_issues"])
    code, _ = json_compare(tmp_path, v1, v2, GATE + "policy:\n  require_same_scenario: false\n")
    assert code == 0
    unversioned, result = json_compare(tmp_path, v1, run_doc("c", VALUES))
    assert unversioned == 0
    assert any("scenario version differs" in i for i in result["run_issues"])


def test_a_stored_baseline_of_another_scenario_version_is_not_used_silently(
    tmp_path: Path,
) -> None:
    store = tmp_path / "store"
    old = write(tmp_path / "old.json", run_doc("b", VALUES, scenario={"id": "s", "version": "1"}))
    new = write(tmp_path / "new.json", run_doc("c", VALUES, scenario={"id": "s", "version": "2"}))
    gate = write(tmp_path / "gate.yaml", GATE)
    saved = runner.invoke(app, ["baseline", "save", str(old), "--store", str(store)])
    assert saved.exit_code == 0, saved.output
    result = runner.invoke(
        app, ["compare", "-b", "@main", "-c", str(new), "-g", str(gate), "--store", str(store)]
    )
    assert result.exit_code == 3, result.output
    assert "scenario version differs" in result.output


def test_comparing_a_file_with_itself_is_an_error(tmp_path: Path) -> None:
    b = write(tmp_path / "b.json", run_doc("b", VALUES))
    g = write(tmp_path / "gate.yaml", GATE)
    result = runner.invoke(app, ["compare", "-b", str(b), "-c", str(b), "-g", str(g)])
    assert result.exit_code == 2, result.output
    assert "baseline and candidate are the same file" in result.output


def test_identical_content_from_two_files_is_a_note(tmp_path: Path) -> None:
    """A deterministic simulator can reproduce a run byte for byte; that is not an error."""
    doc = run_doc("b", VALUES)
    code, result = json_compare(tmp_path, doc, doc)
    assert code == 0
    assert any("identical content" in i for i in result["run_issues"])


def test_on_invalid_pass_does_not_disable_the_scenario_check(tmp_path: Path) -> None:
    gate = GATE + "policy:\n  on_invalid: pass\n"
    other = run_doc("c", VALUES, scenario={"id": "other"})
    code, result = json_compare(tmp_path, run_doc("b", VALUES), other, gate)
    assert code == 3
    assert result["verdict"] == "INVALID"


def test_a_gate_cannot_pass_when_no_metric_could_be_compared(tmp_path: Path) -> None:
    gate = GATE + "policy:\n  on_invalid: pass\n"
    nulled = run_doc("c", dict.fromkeys(VALUES))
    code, result = json_compare(tmp_path, run_doc("b", VALUES), nulled, gate)
    assert code == 3
    assert result["verdict"] == "INVALID"
    assert "no gated metric could be compared" in result["summary"]


def test_a_gate_without_required_metrics_says_so(tmp_path: Path) -> None:
    gate = GATE + "    required: false\n"
    worse = run_doc("c", {k: v + 1 for k, v in VALUES.items()})
    code, result = json_compare(tmp_path, run_doc("b", VALUES), worse, gate)
    assert code == 0
    assert result["metrics"][0]["verdict"] == "FAIL"
    assert result["summary"].startswith("no metric is required, so nothing blocks the gate")


SIGNED = {f"t{i}": (1.0 if i % 2 else -1.0) + 1e-9 for i in range(30)}


def test_relative_margin_is_invalid_for_values_of_both_signs(tmp_path: Path) -> None:
    """A mean near zero of a signed metric gives a meaningless, tiny relative margin."""
    gate = "version: 1\nmetrics:\n  err:\n    max_relative_degradation: 0.1\n"
    shifted = run_doc("c", {k: v + 0.01 for k, v in SIGNED.items()})
    code, result = json_compare(tmp_path, run_doc("b", SIGNED), shifted, gate)
    assert code == 3
    metric = result["metrics"][0]
    assert metric["verdict"] == "INVALID"
    assert "both signs" in metric["reasons"][-1]

    both = gate + "    max_absolute_degradation: 0.5\n"
    code, result = json_compare(tmp_path, run_doc("b", SIGNED), shifted, both)
    assert code == 0
    assert result["metrics"][0]["numerical"]["margin"] == 0.5
    assert "both signs" in result["metrics"][0]["numerical"]["margin_basis"]
