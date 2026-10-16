"""The checked-in examples must match what the current code generates."""

from __future__ import annotations

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
