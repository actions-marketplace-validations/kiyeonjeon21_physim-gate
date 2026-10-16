"""The checked-in examples must match what the current code generates."""

from __future__ import annotations

import json
from pathlib import Path

from physim_gate.config import load_config
from physim_gate.demo import fixture_dir, generate_runs
from physim_gate.schema import dump_json, load_run

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_highway_merge_example_matches_fixture() -> None:
    with fixture_dir("regression") as source:
        baseline, candidate = generate_runs(source)
        gate = (source / "gate.yaml").read_text()
    folder = EXAMPLES / "highway-merge"
    assert (folder / "baseline.json").read_text() == dump_json(baseline)
    assert (folder / "candidate.json").read_text() == dump_json(candidate)
    assert (folder / "gate.yaml").read_text() == gate


def test_example_files_are_valid() -> None:
    folder = EXAMPLES / "highway-merge"
    load_run(folder / "baseline.json")
    load_run(folder / "candidate.json")
    load_config(folder / "gate.yaml")
    for path in (
        EXAMPLES / "csv-adapter" / "data" / "gate.yaml",
        EXAMPLES / "gymnasium" / "gate.yaml",
    ):
        load_config(path)


def test_lerobot_pusht_example_verdicts() -> None:
    """The real LeRobot output gives the verdicts its README describes."""
    from physim_gate.adapters.base import get_adapter
    from physim_gate.engine import compare_runs

    folder = EXAMPLES / "lerobot-pusht"
    adapter = get_adapter("lerobot")
    gate = load_config(folder / "gate.yaml")
    baseline = adapter.load(folder / "baseline", {"start_seed": "1000"})

    rerun = adapter.load(folder / "baseline-rerun", {"start_seed": "1000"})
    same = compare_runs(baseline, rerun, gate)
    assert same.verdict.value == "PASS"
    assert same.exit_code == 0

    candidate = adapter.load(folder / "candidate-n-action-steps-8", {"start_seed": "1000"})
    changed = compare_runs(baseline, candidate, gate)
    verdicts = {m.name: m.verdict.value for m in changed.metrics}
    assert verdicts == {"success": "INCONCLUSIVE", "max_reward": "FAIL", "sum_reward": "PASS"}
    assert changed.exit_code == 1
    success = next(m for m in changed.metrics if m.name == "success")
    assert success.evidence.degradation_ci is not None
    low, high = success.evidence.degradation_ci
    assert (round(low, 3), round(high, 3)) == (0.034, 0.177)


def test_lerobot_metaworld_example_verdicts() -> None:
    """Real multi-task output: four MetaWorld tasks, 25 episodes each."""
    from physim_gate.adapters.base import get_adapter
    from physim_gate.engine import compare_runs

    folder = EXAMPLES / "lerobot-metaworld"
    adapter = get_adapter("lerobot")
    options = {"start_seed": "1000", "scenario": "metaworld"}
    baseline = adapter.load(folder / "baseline", options)
    candidate = adapter.load(folder / "candidate-n-action-steps-10", options)
    assert len(baseline.trials) == 100
    assert {t.labels["task_group"] for t in baseline.trials} == {
        "reach-v3",
        "button-press-v3",
        "drawer-open-v3",
        "door-open-v3",
    }
    result = compare_runs(baseline, candidate, load_config(folder / "gate.yaml"))
    assert result.verdict.value == "PASS"
    success = next(m for m in result.metrics if m.name == "success")
    assert success.numerical is not None
    assert (success.numerical.baseline, success.numerical.candidate) == (0.68, 0.84)


def test_lerobot_suite_example(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from physim_gate.cli import app

    result = CliRunner().invoke(
        app, ["suite", str(EXAMPLES / "lerobot-suite.yaml"), "-o", str(tmp_path), "-f", "json"]
    )
    assert result.exit_code == 1, result.output
    verdicts = {s["name"]: s["verdict"] for s in json.loads(result.stdout)["scenarios"]}
    assert verdicts == {"pusht": "FAIL", "metaworld": "PASS"}
