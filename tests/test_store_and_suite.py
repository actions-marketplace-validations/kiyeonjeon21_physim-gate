from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from typer.testing import CliRunner

from physim_gate.cli import app
from physim_gate.schema import SchemaError, load_run
from physim_gate.store import (
    StoreError,
    baseline_path,
    list_baselines,
    load_baseline,
    save_baseline,
    scenario_directory,
)
from physim_gate.suite import load_suite

EXAMPLES = Path(__file__).parent.parent / "examples"
HIGHWAY = EXAMPLES / "highway-merge"
PUSHT = EXAMPLES / "lerobot-pusht"
LEROBOT = ("-a", "lerobot", "-O", "start_seed=1000")
runner = CliRunner()


@pytest.fixture(autouse=True)
def _plain_output(no_color: None) -> None:
    """Stable, uncolored, wide output for assertions."""


def test_store_round_trip(tmp_path: Path) -> None:
    run = load_run(HIGHWAY / "baseline.json")
    path = save_baseline(run, "main", source=HIGHWAY / "baseline.json", store=tmp_path)
    assert path == tmp_path / "highway_on_ramp_merge" / "main.json"
    stored_path, stored = load_baseline("highway_on_ramp_merge", "main", tmp_path)
    assert stored_path == path
    assert stored.trials == run.trials
    assert stored.labels["physim-gate.baseline.source"] == str(HIGHWAY / "baseline.json")
    assert len(stored.labels["physim-gate.baseline.source_sha256"]) == 64
    with pytest.raises(StoreError, match="already exists"):
        save_baseline(run, "main", source=HIGHWAY / "baseline.json", store=tmp_path)
    save_baseline(run, "main", source=HIGHWAY / "baseline.json", store=tmp_path, force=True)
    [listed] = list_baselines(tmp_path)
    assert (listed.scenario, listed.name, listed.error) == ("highway_on_ramp_merge", "main", None)


def test_store_names_and_lookups(tmp_path: Path) -> None:
    assert scenario_directory("libero_spatial+libero_object") == "libero_spatial_libero_object"
    assert scenario_directory("../..") == "_"
    for bad in ("../x", "", ".hidden", "a/b", ".."):
        with pytest.raises(StoreError, match="invalid baseline name"):
            baseline_path("s", bad, tmp_path)
    with pytest.raises(StoreError, match="no baseline 'main' for scenario 's'"):
        load_baseline("s", "main", tmp_path)
    run = load_run(HIGHWAY / "baseline.json")
    save_baseline(run, "v1", source=HIGHWAY / "baseline.json", store=tmp_path)
    with pytest.raises(StoreError, match="stored for this scenario: v1"):
        load_baseline("highway_on_ramp_merge", "main", tmp_path)
    # A scenario id that maps to the same directory is not silently confused with another.
    with pytest.raises(StoreError, match="holds scenario 'highway_on_ramp_merge'"):
        load_baseline("highway/on/ramp/merge", "v1", tmp_path)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "x.json").write_text("{")
    broken = [b for b in list_baselines(tmp_path) if b.error]
    assert len(broken) == 1
    assert "invalid JSON" in (broken[0].error or "")


def test_store_location_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHYSIM_GATE_STORE", str(tmp_path / "env-store"))
    assert baseline_path("s", "main") == tmp_path / "env-store" / "s" / "main.json"


def test_baseline_cli_and_at_reference(tmp_path: Path) -> None:
    store = str(tmp_path / "store")
    assert "no baselines" in runner.invoke(app, ["baseline", "list", "--store", store]).output
    saved = runner.invoke(
        app,
        ["baseline", "save", str(PUSHT / "baseline"), *LEROBOT, "--store", store],
    )
    assert saved.exit_code == 0, saved.output
    assert "saved pusht @main (baseline, 100 trials)" in saved.output
    listed = runner.invoke(app, ["baseline", "list", "--store", store])
    assert "@main" in listed.output
    as_json = json.loads(
        runner.invoke(app, ["baseline", "list", "--store", store, "-f", "json"]).stdout
    )
    assert as_json[0]["scenario"] == "pusht"
    path = runner.invoke(app, ["baseline", "path", "pusht", "--store", store])
    assert path.stdout.strip().endswith("pusht/main.json")

    compared = runner.invoke(
        app,
        [
            "compare",
            *LEROBOT,
            *("-b", "@main", "--store", store),
            *("-c", str(PUSHT / "candidate-n-action-steps-8"), "-g", str(PUSHT / "gate.yaml")),
            *("-f", "json"),
        ],
    )
    assert compared.exit_code == 1
    document = json.loads(compared.stdout)
    assert document["inputs"]["baseline"]["path"].endswith("pusht/main.json")
    assert document["verdict"] == "FAIL"

    missing = runner.invoke(
        app,
        [
            *("compare", "-b", "@main", "--store", store),
            *("-c", str(HIGHWAY / "candidate.json"), "-g", str(HIGHWAY / "gate.yaml")),
        ],
    )
    assert missing.exit_code == 2
    assert "no baseline 'main' for scenario 'highway_on_ramp_merge'" in missing.output
    again = runner.invoke(
        app, ["baseline", "save", str(HIGHWAY / "baseline.json"), "--store", store]
    )
    assert again.exit_code == 0
    duplicate = runner.invoke(
        app, ["baseline", "save", str(HIGHWAY / "baseline.json"), "--store", store]
    )
    assert duplicate.exit_code == 2
    assert "already exists" in duplicate.output


def _write_suite(tmp_path: Path, scenarios: str) -> Path:
    path = tmp_path / "suite.yaml"
    path.write_text(
        "version: 1\n"
        "defaults:\n"
        "  adapter: lerobot\n"
        "  adapter_options: {start_seed: '1000'}\n"
        f"  config: {PUSHT / 'gate.yaml'}\n"
        "scenarios:\n" + scenarios
    )
    return path


SCENARIOS = f"""\
  pusht-rerun:
    baseline: {PUSHT / "baseline"}
    candidate: {PUSHT / "baseline-rerun"}
  pusht-n8:
    baseline: {PUSHT / "baseline"}
    candidate: {PUSHT / "candidate-n-action-steps-8"}
  highway:
    adapter: json
    baseline: {HIGHWAY / "baseline.json"}
    candidate: {HIGHWAY / "candidate.json"}
    config: {HIGHWAY / "gate.yaml"}
"""


def test_suite_runs_every_scenario_and_aggregates(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path, SCENARIOS)
    out = tmp_path / "out"
    result = runner.invoke(app, ["suite", str(suite), "-o", str(out)])
    assert result.exit_code == 1, result.output
    assert "3 scenarios: 2 FAIL, 1 PASS" in result.output
    for name in ("pusht-rerun", "pusht-n8", "highway"):
        for file in ("comparison.json", "junit.xml", "report.html", "summary.md"):
            assert (out / name / file).is_file()
    document = json.loads((out / "suite.json").read_text())
    assert document["exit_code"] == 1
    assert [s["verdict"] for s in document["scenarios"]] == ["PASS", "FAIL", "FAIL"]
    assert "result" not in document["scenarios"][0]
    junit = ET.fromstring((out / "junit.xml").read_text())
    suites = junit.findall("testsuite")
    assert [s.get("name") for s in suites] == [
        "physim-gate.pusht-rerun",
        "physim-gate.pusht-n8",
        "physim-gate.highway",
    ]
    assert junit.get("tests") == str(sum(int(s.get("tests", "0")) for s in suites))
    markdown = (out / "summary.md").read_text()
    assert markdown.startswith("<!-- physim-gate -->\n### ❌ PhySim Gate suite: FAIL")
    for name in ("pusht-rerun", "pusht-n8", "highway"):
        assert f"<details><summary>{name}</summary>" in markdown
    assert "#### ❌ PhySim Gate: FAIL" in markdown
    assert markdown.count("<!-- physim-gate -->") == 1


def test_suite_errors_do_not_stop_other_scenarios(tmp_path: Path) -> None:
    suite = _write_suite(
        tmp_path,
        SCENARIOS
        + "  broken:\n    baseline: '@main'\n    candidate: missing-dir\n"
        + "  typo:\n    baseline: x\n    candidate: y\n    adapter: nope\n",
    )
    result = runner.invoke(app, ["suite", str(suite), "-o", str(tmp_path / "out")])
    assert result.exit_code == 2, result.output
    # The whole path stays on one line, however long (macOS temporary directories are long).
    assert f"error: {tmp_path / 'missing-dir'}: no such file\n" in result.output
    junit = ET.fromstring((tmp_path / "out" / "junit.xml").read_text())
    assert junit.get("errors") is not None
    assert int(junit.get("errors", "0")) >= 2
    as_markdown = runner.invoke(
        app, ["suite", str(suite), "-o", str(tmp_path / "out"), "-f", "markdown"]
    )
    assert "| `broken` | ⛔\u00a0ERROR | Could not be compared" in as_markdown.stdout


def test_suite_exit_code_for_blocked_scenarios(tmp_path: Path) -> None:
    gate = tmp_path / "strict.yaml"
    gate.write_text("version: 1\nmetrics: {success: {max_absolute_degradation: 0.0001}}\n")
    suite = _write_suite(
        tmp_path,
        f"  same:\n    baseline: {PUSHT / 'baseline'}\n    candidate: {PUSHT / 'baseline-rerun'}\n"
        f"    config: {gate}\n",
    )
    result = runner.invoke(app, ["suite", str(suite), "-o", str(tmp_path / "out"), "-f", "json"])
    assert result.exit_code == 3, result.output
    assert json.loads(result.stdout)["scenarios"][0]["verdict"] == "INCONCLUSIVE"


def test_suite_file_validation_and_paths(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    suite = tmp_path / "sub" / "suite.yaml"
    suite.write_text(
        "version: 1\ndefaults: {config: gate.yaml, adapter_options: {a: '1'}}\n"
        "scenarios:\n"
        "  one: {baseline: '@main', candidate: runs/one}\n"
        "  two: {baseline: b, candidate: /abs/c, adapter: csv}\n"
        "  three: {baseline: b, candidate: c, adapter_options: {}}\n"
    )
    one, two, three = load_suite(suite)
    assert one.baseline == Path("@main")
    assert one.candidate == tmp_path / "sub" / "runs" / "one"
    assert one.config == tmp_path / "sub" / "gate.yaml"
    assert one.adapter_options == {"a": "1"}
    assert two.candidate == Path("/abs/c")
    assert two.adapter_options == {}  # another adapter does not inherit the defaults' options
    assert three.adapter_options == {}

    suite.write_text("version: 1\nscenarios: {one: {baseline: b, candidate: c}}\n")
    with pytest.raises(SchemaError, match="have no config"):
        load_suite(suite)
    suite.write_text("version: 1\nscenarios: {'bad name': {baseline: b, candidate: c}}\n")
    with pytest.raises(SchemaError, match="not a valid suite"):
        load_suite(suite)
    suite.write_text("- a list\n")
    with pytest.raises(SchemaError, match="expected a mapping"):
        load_suite(suite)
    with pytest.raises(SchemaError, match="cannot read file"):
        load_suite(tmp_path / "missing.yaml")
