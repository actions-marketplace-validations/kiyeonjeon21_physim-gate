from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from physim_gate import __version__
from physim_gate.cli import app
from physim_gate.demo import fixture_dir, generate_runs
from physim_gate.schema import dump_json

runner = CliRunner()


@pytest.fixture(autouse=True)
def _plain_output(no_color: None) -> None:
    """Stable, uncolored, wide output for assertions."""


def materialize(tmp_path: Path, scenario: str) -> tuple[Path, Path, Path]:
    with fixture_dir(scenario) as source:
        baseline, candidate = generate_runs(source)
        gate = (source / "gate.yaml").read_text()
    paths = tmp_path / "baseline.json", tmp_path / "candidate.json", tmp_path / "gate.yaml"
    paths[0].write_text(dump_json(baseline))
    paths[1].write_text(dump_json(candidate))
    paths[2].write_text(gate)
    return paths


def compare_args(paths: tuple[Path, Path, Path], *extra: str) -> list[str]:
    b, c, g = paths
    return ["compare", "-b", str(b), "-c", str(c), "-g", str(g), *extra]


@pytest.mark.parametrize(
    ("scenario", "code", "verdict"),
    [
        ("no_regression", 0, "PASS"),
        ("regression", 1, "FAIL"),
        ("insufficient_evidence", 3, "INCONCLUSIVE"),
        ("invalid_comparison", 3, "INVALID"),
    ],
)
def test_compare_exit_codes(tmp_path: Path, scenario: str, code: int, verdict: str) -> None:
    result = runner.invoke(app, compare_args(materialize(tmp_path, scenario)))
    assert result.exit_code == code, result.output
    assert verdict in result.output


def test_compare_writes_all_outputs_and_report_round_trips(tmp_path: Path) -> None:
    paths = materialize(tmp_path, "regression")
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        compare_args(
            paths,
            "--output",
            str(out / "comparison.json"),
            "--junit",
            str(out / "junit.xml"),
            "--html",
            str(out / "report.html"),
            "--markdown",
            str(out / "summary.md"),
        ),
    )
    assert result.exit_code == 1
    assert "summary.md" in result.output
    assert (out / "summary.md").read_text().startswith("<!-- physim-gate -->\n### ❌")
    comparison = json.loads((out / "comparison.json").read_text())
    assert comparison["kind"] == "physim-gate.comparison"
    assert comparison["inputs"]["baseline"]["sha256"]
    assert (out / "junit.xml").read_text().startswith("<?xml")
    assert "<!doctype html>" in (out / "report.html").read_text()

    rerender = runner.invoke(
        app, ["report", str(out / "comparison.json"), "--html", str(tmp_path / "again.html")]
    )
    assert rerender.exit_code == 1
    assert (tmp_path / "again.html").read_text() == (out / "report.html").read_text()


def test_compare_json_format_is_machine_readable(tmp_path: Path) -> None:
    result = runner.invoke(
        app, compare_args(materialize(tmp_path, "no_regression"), "--format", "json")
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["verdict"] == "PASS"
    assert data["exit_code"] == 0


def test_written_paths_are_not_wrapped(tmp_path: Path) -> None:
    deep = tmp_path / ("very-long-directory-name-" * 6) / "reports"
    paths = materialize(tmp_path, "no_regression")
    result = runner.invoke(
        app, compare_args(paths, "--output", str(deep / "c.json"), "--html", str(deep / "r.html"))
    )
    assert result.exit_code == 0
    assert f"wrote {deep / 'c.json'}, {deep / 'r.html'}\n" in result.output


def test_markdown_format_on_stdout(tmp_path: Path) -> None:
    paths = materialize(tmp_path, "insufficient_evidence")
    saved = str(tmp_path / "c.json")
    result = runner.invoke(app, compare_args(paths, "--format", "markdown", "-o", saved))
    assert result.exit_code == 3
    assert result.stdout.startswith("<!-- physim-gate -->\n### ⚠️ PhySim Gate: INCONCLUSIVE")
    assert "wrote" not in result.stdout
    report = runner.invoke(app, ["report", saved, "-f", "markdown"])
    assert report.exit_code == 3
    assert report.stdout == result.stdout
    # Formats that only make sense for comparison results are not offered elsewhere.
    assert runner.invoke(app, ["adapters", "-f", "markdown"]).exit_code == 2


def test_compare_with_lerobot_adapter(tmp_path: Path) -> None:
    real = Path(__file__).parent / "data" / "lerobot" / "pusht_act_4_episodes.json"
    gate = tmp_path / "gate.yaml"
    gate.write_text(
        "version: 1\ndefaults: {min_samples: 2}\n"
        "metrics: {success: {max_absolute_degradation: 0.9}, sum_reward: "
        "{max_relative_degradation: 0.5}}\n"
    )
    rerun = tmp_path / "rerun.json"
    rerun.write_bytes(real.read_bytes())
    args = ["-a", "lerobot", "-O", "start_seed=1000", "-g", str(gate), "-f", "json"]
    result = runner.invoke(app, ["compare", "-b", str(real), "-c", str(rerun), *args])
    data = json.loads(result.stdout)
    assert [m["pairing"]["mode"] for m in data["metrics"]] == ["paired", "paired"]
    assert data["baseline"]["seeds"] == [1000, 1001, 1002, 1003]
    assert data["inputs"]["adapter"] == "lerobot"


def test_compare_usage_errors_exit_2(tmp_path: Path) -> None:
    b, c, g = materialize(tmp_path, "regression")
    missing = runner.invoke(
        app, ["compare", "-b", str(tmp_path / "nope.json"), "-c", str(c), "-g", str(g)]
    )
    assert missing.exit_code == 2
    assert "no such file" in missing.output

    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("version: 1\nmetrics: {x: {}}\n")
    invalid = runner.invoke(app, ["compare", "-b", str(b), "-c", str(c), "-g", str(bad_config)])
    assert invalid.exit_code == 2
    assert "not a valid gate config" in invalid.output

    broken = tmp_path / "broken.json"
    broken.write_text("{")
    bad_run = runner.invoke(app, ["compare", "-b", str(broken), "-c", str(c), "-g", str(g)])
    assert bad_run.exit_code == 2
    assert "invalid JSON" in bad_run.output

    unknown = runner.invoke(app, [*compare_args((b, c, g)), "--adapter", "carla"])
    assert unknown.exit_code == 2
    assert "unknown adapter 'carla'" in unknown.output

    no_args = runner.invoke(app, ["compare"])
    assert no_args.exit_code == 2


def test_internal_errors_exit_4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = materialize(tmp_path, "regression")

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr("physim_gate.cli.compare_runs", boom)
    result = runner.invoke(app, compare_args(paths))
    assert result.exit_code == 4
    assert "internal error" in result.output
    assert "kaboom" in result.output


def test_report_rejects_run_files(tmp_path: Path) -> None:
    b, _, _ = materialize(tmp_path, "regression")
    result = runner.invoke(app, ["report", str(b)])
    assert result.exit_code == 2
    assert "not a comparison result" in result.output


def test_validate(tmp_path: Path) -> None:
    b, _, _ = materialize(tmp_path, "regression")
    ok = runner.invoke(app, ["validate", str(b)])
    assert ok.exit_code == 0
    assert "valid:" in ok.output

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"run_id": "x"}))
    invalid = runner.invoke(app, ["validate", str(bad)])
    assert invalid.exit_code == 1
    assert "simulator: Field required" in invalid.output

    as_json = runner.invoke(app, ["validate", str(bad), "--format", "json"])
    assert as_json.exit_code == 1
    assert json.loads(as_json.stdout)["valid"] is False

    missing = runner.invoke(app, ["validate", str(tmp_path / "missing.json")])
    assert missing.exit_code == 2


def test_validate_strict_treats_warnings_as_errors(tmp_path: Path) -> None:
    run = {
        "run_id": "r",
        "simulator": {"name": "s"},
        "scenario": {"id": "x"},
        "metrics": {"m": {"direction": "higher_is_better"}},
        "trials": [{"trial_id": "a", "metrics": {"m": 1}}],
    }
    path = tmp_path / "run.json"
    path.write_text(json.dumps(run))
    assert runner.invoke(app, ["validate", str(path)]).exit_code == 0
    strict = runner.invoke(app, ["validate", str(path), "--strict"])
    assert strict.exit_code == 1
    assert "warning:" in strict.output


def test_init(tmp_path: Path) -> None:
    target = tmp_path / "gate.yaml"
    assert runner.invoke(app, ["init", str(target)]).exit_code == 0
    assert "max_absolute_degradation" in target.read_text()
    again = runner.invoke(app, ["init", str(target)])
    assert again.exit_code == 2
    assert "already exists" in again.output

    b, c, _ = materialize(tmp_path, "regression")
    derived = tmp_path / "derived.yaml"
    assert runner.invoke(app, ["init", str(derived), "--from", str(b)]).exit_code == 0
    text = derived.read_text()
    assert "collision:" in text
    assert "physim.failure_rate:" in text
    # The generated config is immediately usable.
    result = runner.invoke(app, ["compare", "-b", str(b), "-c", str(c), "-g", str(derived)])
    assert result.exit_code in (0, 1, 3)
    assert "error:" not in result.output


def test_demo(tmp_path: Path) -> None:
    result = runner.invoke(app, ["demo", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert result.output.count("as expected") == 4
    for scenario in ("no_regression", "regression", "insufficient_evidence", "invalid_comparison"):
        for name in (
            "baseline.json",
            "candidate.json",
            "gate.yaml",
            "comparison.json",
            "junit.xml",
            "report.html",
        ):
            assert (tmp_path / scenario / name).is_file()


def test_adapters_and_schema_and_version() -> None:
    listed = runner.invoke(app, ["adapters"])
    assert listed.exit_code == 0
    assert "json" in listed.output
    assert "synthetic" in listed.output
    as_json = json.loads(runner.invoke(app, ["adapters", "--format", "json"]).stdout)
    assert {a["name"] for a in as_json} >= {"json", "synthetic", "gymnasium", "lerobot"}
    for kind in ("run", "comparison", "config"):
        schema = runner.invoke(app, ["schema", kind])
        assert schema.exit_code == 0
        assert "properties" in json.loads(schema.stdout)
    assert runner.invoke(app, ["--version"]).stdout.strip() == f"physim-gate {__version__}"


def test_help_documents_exit_codes() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Exit codes" in result.output
    assert "compare" in result.output
    no_args = runner.invoke(app, [])
    assert "Usage" in no_args.output


def test_console_script_end_to_end(tmp_path: Path) -> None:
    """Run the installed entry point in a real subprocess, as CI would."""
    paths = materialize(tmp_path, "regression")
    env = {**os.environ, "NO_COLOR": "1"}
    script = Path(sys.executable).parent / "physim-gate"
    command = [str(script)] if script.exists() else [sys.executable, "-m", "physim_gate"]
    completed = subprocess.run(
        [*command, *compare_args(paths, "--junit", str(tmp_path / "j.xml"))],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert completed.returncode == 1, completed.stderr
    assert "FAIL" in completed.stdout
    module = subprocess.run(
        [sys.executable, "-m", "physim_gate", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert module.stdout.strip() == f"physim-gate {__version__}"


def test_validate_unreadable_input_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code == 2
    assert "cannot read file" in result.output


def test_directory_inputs_are_hashed_not_crashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adapters may read a directory per run; provenance must hash it instead of crashing."""
    b, c, g = materialize(tmp_path, "regression")
    for name, source in (("base_dir", b), ("cand_dir", c)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "run.json").write_text(source.read_text())

    class DirAdapter:
        name = "dir"
        description = "reads run.json from a directory"

        def load(self, source: Path, options: object) -> object:
            from physim_gate.schema import load_run

            return load_run(source / "run.json")

    monkeypatch.setattr("physim_gate.adapters.base.get_adapter", lambda _name: DirAdapter())
    out = tmp_path / "comparison.json"
    args = [
        "compare",
        "-b",
        str(tmp_path / "base_dir"),
        "-c",
        str(tmp_path / "cand_dir"),
        "-g",
        str(g),
    ]
    result = runner.invoke(app, [*args, "--adapter", "dir", "-o", str(out)])
    assert result.exit_code == 1, result.output
    digest = json.loads(out.read_text())["inputs"]["baseline"]["sha256"]
    assert len(digest) == 64
    (tmp_path / "base_dir" / "extra.txt").write_text("changed")
    runner.invoke(app, [*args, "--adapter", "dir", "-o", str(out)])
    assert json.loads(out.read_text())["inputs"]["baseline"]["sha256"] != digest


def test_init_passes_adapter_options(tmp_path: Path) -> None:
    with fixture_dir("regression") as source:
        spec = source / "baseline.yaml"
        target = tmp_path / "gate.yaml"
        result = runner.invoke(
            app,
            ["init", str(target), "--adapter", "synthetic", "--from", str(spec), "-O", "trials=5"],
        )
    assert result.exit_code == 0, result.output
    assert "collision:" in target.read_text()
    bad = runner.invoke(
        app, ["init", str(tmp_path / "x.yaml"), "--from", str(spec), "-O", "nope=1"]
    )
    assert bad.exit_code == 2


@pytest.mark.parametrize(("scenario", "code"), [("regression", 1), ("insufficient_evidence", 3)])
def test_exit_code_survives_a_closed_stdout(tmp_path: Path, scenario: str, code: int) -> None:
    """`physim-gate compare ... | head -1` must keep the verdict's exit code."""
    paths = materialize(tmp_path, scenario)
    script = Path(sys.executable).parent / "physim-gate"
    command = [str(script)] if script.exists() else [sys.executable, "-m", "physim_gate"]
    process = subprocess.Popen(
        [*command, *compare_args(paths)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "NO_COLOR": "1", "COLUMNS": "120"},
    )
    assert process.stdout is not None
    assert process.stderr is not None
    process.stdout.readline()
    process.stdout.close()
    stderr = process.stderr.read().decode()
    assert process.wait() == code, stderr
    process.stderr.close()
