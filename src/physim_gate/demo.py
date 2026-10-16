"""``physim-gate demo``: run the real pipeline on the bundled synthetic scenarios.

Each scenario's baseline and candidate runs are generated from YAML specs by the synthetic
adapter, compared by the engine under the scenario's ``gate.yaml``, and rendered to JSON, JUnit
and HTML. Nothing is precomputed: the verdicts come from the comparison engine.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from physim_gate.adapters.synthetic import SyntheticAdapter
from physim_gate.comparison import ComparisonResult, InputRef, Inputs, Verdict
from physim_gate.config import load_config
from physim_gate.engine import compare_runs
from physim_gate.provenance import sha256_file
from physim_gate.reporting.html import render_html
from physim_gate.reporting.junit import render_junit
from physim_gate.schema import RunResult, dump_json


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    expected: Verdict


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "no_regression",
        "Robot pick-and-place: a refactored grasp policy that behaves the same",
        Verdict.PASS,
    ),
    Scenario(
        "regression",
        "Highway merge: a planner change that makes the vehicle merge more aggressively",
        Verdict.FAIL,
    ),
    Scenario(
        "insufficient_evidence",
        "Drone hover in gusts: only 6 flights per side, too few to decide",
        Verdict.INCONCLUSIVE,
    ),
    Scenario(
        "invalid_comparison",
        "Quadruped stairs: the candidate run stopped reporting a gated metric",
        Verdict.INVALID,
    ),
)


@contextmanager
def fixture_dir(name: str) -> Iterator[Path]:
    """Filesystem path of a bundled fixture directory."""
    with resources.as_file(resources.files("physim_gate.fixtures") / name) as path:
        yield path


def generate_runs(directory: Path) -> tuple[RunResult, RunResult]:
    adapter = SyntheticAdapter()
    return (
        adapter.load(directory / "baseline.yaml", {}),
        adapter.load(directory / "candidate.yaml", {}),
    )


@dataclass(frozen=True)
class DemoOutcome:
    scenario: Scenario
    result: ComparisonResult
    directory: Path

    @property
    def as_expected(self) -> bool:
        return self.result.verdict is self.scenario.expected


def run_demo(
    output_dir: Path, on_result: Callable[[DemoOutcome], None] | None = None
) -> list[DemoOutcome]:
    outcomes = []
    for scenario in SCENARIOS:
        target = output_dir / scenario.name
        target.mkdir(parents=True, exist_ok=True)
        with fixture_dir(scenario.name) as source:
            baseline, candidate = generate_runs(source)
            config_text = (source / "gate.yaml").read_text(encoding="utf-8")
        baseline_path = target / "baseline.json"
        candidate_path = target / "candidate.json"
        config_path = target / "gate.yaml"
        baseline_path.write_text(dump_json(baseline), encoding="utf-8")
        candidate_path.write_text(dump_json(candidate), encoding="utf-8")
        config_path.write_text(config_text, encoding="utf-8")

        inputs = Inputs(
            baseline=InputRef(path=baseline_path.name, sha256=sha256_file(baseline_path)),
            candidate=InputRef(path=candidate_path.name, sha256=sha256_file(candidate_path)),
            config=InputRef(path=config_path.name, sha256=sha256_file(config_path)),
            adapter="synthetic",
        )
        result = compare_runs(baseline, candidate, load_config(config_path), inputs=inputs)
        (target / "comparison.json").write_text(dump_json(result), encoding="utf-8")
        (target / "junit.xml").write_text(render_junit(result), encoding="utf-8")
        (target / "report.html").write_text(render_html(result), encoding="utf-8")
        outcome = DemoOutcome(scenario=scenario, result=result, directory=target)
        outcomes.append(outcome)
        if on_result is not None:
            on_result(outcome)
    return outcomes
