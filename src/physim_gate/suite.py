"""Suites: gate several scenarios with one command and one aggregated verdict.

A suite file lists scenarios, each with a baseline, a candidate and a gate config::

    version: 1
    defaults:
      adapter: lerobot
      adapter_options: {start_seed: "1000"}
      config: gate.yaml
    scenarios:
      pusht:
        baseline: "@main"              # a stored baseline, or a path
        candidate: runs/pusht
      metaworld:
        baseline: baselines/metaworld
        candidate: runs/metaworld
        config: metaworld-gate.yaml

Relative paths are resolved against the suite file's directory. A scenario's own
``adapter_options`` replace the defaults, and a scenario that uses a different adapter than
the defaults does not inherit the default options. Each scenario is compared
exactly as ``physim-gate compare`` would; the suite's exit code is the most severe one:
2 if any scenario could not be compared at all, else 1 if any regressed, else 3 if any is
blocked as inconclusive or invalid, else 0.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from physim_gate.adapters.base import AdapterError
from physim_gate.comparison import ComparisonResult, ExitCode, Verdict
from physim_gate.reporting.junit import render_junit
from physim_gate.reporting.markdown import MARKER, NBSP, cell, code_span, render_markdown, sentence
from physim_gate.schema import InputError, SchemaError, format_validation_error
from physim_gate.store import is_reference

SUITE_VERSION = 1


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScenarioDefaults(_Model):
    adapter: str = "json"
    adapter_options: dict[str, str] = Field(default_factory=dict)
    config: str | None = None


class ScenarioEntry(_Model):
    baseline: str
    candidate: str
    config: str | None = None
    adapter: str | None = None
    adapter_options: dict[str, str] | None = None


ScenarioName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]


class SuiteFile(_Model):
    version: Literal[1] = 1
    defaults: ScenarioDefaults = Field(default_factory=ScenarioDefaults)
    scenarios: Annotated[dict[ScenarioName, ScenarioEntry], Field(min_length=1)]

    @model_validator(mode="after")
    def _configs(self) -> SuiteFile:
        missing = [
            n for n, s in self.scenarios.items() if (s.config or self.defaults.config) is None
        ]
        if missing:
            raise ValueError(
                f"scenario(s) {', '.join(missing)} have no config; set 'config' on the scenario "
                "or in 'defaults'"
            )
        return self


class ResolvedScenario(_Model):
    name: str
    baseline: Path
    candidate: Path
    config: Path
    adapter: str
    adapter_options: dict[str, str]


class ScenarioOutcome(_Model):
    name: str
    exit_code: int
    verdict: Verdict | None = None
    summary: str
    error: str | None = None
    result: ComparisonResult | None = None


class SuiteResult(_Model):
    kind: Literal["physim-gate.suite"] = "physim-gate.suite"
    exit_code: int
    summary: str
    scenarios: list[ScenarioOutcome]


def load_suite(path: Path) -> list[ResolvedScenario]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"{path}: cannot read file ({exc.strerror or exc})") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SchemaError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaError(f"{path}: expected a mapping at the top level")
    try:
        suite = SuiteFile.model_validate(data)
    except ValidationError as exc:
        raise SchemaError(f"{path}: not a valid suite", format_validation_error(exc)) from exc

    root = path.parent

    def resolve(value: str) -> Path:
        if is_reference(value):
            return Path(value)
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else root / candidate

    scenarios = []
    for name, entry in suite.scenarios.items():
        config = entry.config or suite.defaults.config
        assert config is not None
        scenarios.append(
            ResolvedScenario(
                name=name,
                baseline=resolve(entry.baseline),
                candidate=resolve(entry.candidate),
                config=resolve(config),
                adapter=entry.adapter or suite.defaults.adapter,
                adapter_options=_options(entry, suite.defaults),
            )
        )
    return scenarios


def _options(entry: ScenarioEntry, defaults: ScenarioDefaults) -> dict[str, str]:
    """A scenario's own options replace the defaults; options of another adapter never apply."""
    if entry.adapter_options is not None:
        return dict(entry.adapter_options)
    if entry.adapter is not None and entry.adapter != defaults.adapter:
        return {}
    return dict(defaults.adapter_options)


Comparer = Callable[[ResolvedScenario], ComparisonResult]


def run_suite(scenarios: list[ResolvedScenario], compare: Comparer) -> SuiteResult:
    """Compare every scenario; errors in one scenario do not stop the others."""
    outcomes = []
    for scenario in scenarios:
        try:
            result = compare(scenario)
        except (SchemaError, AdapterError) as exc:
            outcomes.append(
                ScenarioOutcome(
                    name=scenario.name,
                    exit_code=int(ExitCode.USAGE),
                    summary="could not be compared",
                    error=str(exc),
                )
            )
            continue
        outcomes.append(
            ScenarioOutcome(
                name=scenario.name,
                exit_code=result.exit_code,
                verdict=result.verdict,
                summary=result.summary,
                result=result,
            )
        )
    codes = {o.exit_code for o in outcomes}
    for code in (ExitCode.USAGE, ExitCode.REGRESSION, ExitCode.UNCERTAIN, ExitCode.OK):
        if int(code) in codes:
            exit_code = int(code)
            break
    else:  # pragma: no cover - every outcome has one of the codes above
        exit_code = max(codes)
    counts: dict[str, int] = {}
    for outcome in outcomes:
        label = outcome.verdict.value if outcome.verdict else "ERROR"
        counts[label] = counts.get(label, 0) + 1
    order = ("FAIL", "ERROR", "INVALID", "INCONCLUSIVE", "PASS")
    summary = f"{len(outcomes)} scenarios: " + ", ".join(
        f"{counts[label]} {label}" for label in order if label in counts
    )
    return SuiteResult(exit_code=exit_code, summary=summary, scenarios=outcomes)


def render_suite_junit(suite: SuiteResult) -> str:
    """One ``testsuite`` per scenario, as ``physim-gate compare --junit`` writes it."""
    root = ET.Element("testsuites", {"name": "physim-gate"})
    totals = dict.fromkeys(("tests", "failures", "errors", "skipped"), 0)
    for outcome in suite.scenarios:
        if outcome.result is not None:
            scenario_root = ET.fromstring(render_junit(outcome.result))
            for element in scenario_root.findall("testsuite"):
                element.set("name", f"physim-gate.{outcome.name}")
                for key in totals:
                    totals[key] += int(element.get(key, "0"))
                root.append(element)
            continue
        element = ET.SubElement(
            root,
            "testsuite",
            {"name": f"physim-gate.{outcome.name}", "tests": "1", "errors": "1"},
        )
        case = ET.SubElement(
            element, "testcase", {"classname": f"physim-gate.{outcome.name}", "name": "load"}
        )
        error = ET.SubElement(case, "error", {"message": outcome.error or "", "type": "input"})
        error.text = outcome.error
        totals["tests"] += 1
        totals["errors"] += 1
    for key, value in totals.items():
        root.set(key, str(value))
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


_ICON = {"PASS": "✅", "FAIL": "❌", "INCONCLUSIVE": "⚠️", "INVALID": "⛔", "ERROR": "⛔"}
_OVERALL = {0: "PASS", 1: "FAIL", 2: "ERROR", 3: "BLOCKED"}


def render_suite_markdown(suite: SuiteResult, *, max_chars: int = 60_000) -> str:
    """A suite overview table, then each scenario's summary in a collapsed section."""
    overall = _OVERALL.get(suite.exit_code, "ERROR")
    icon = {"PASS": "✅", "FAIL": "❌", "BLOCKED": "⚠️"}.get(overall, "⛔")
    lines = [
        MARKER,
        f"### {icon} PhySim Gate suite: {overall}",
        "",
        cell(sentence(suite.summary)),
        "",
        "| Scenario | Verdict | Summary |",
        "|---|---|---|",
    ]
    for outcome in suite.scenarios:
        label = outcome.verdict.value if outcome.verdict else "ERROR"
        detail = outcome.summary if outcome.error is None else f"{outcome.summary}: {outcome.error}"
        lines.append(
            f"| {code_span(outcome.name)} | {_ICON[label]}{NBSP}{label} | "
            f"{cell(sentence(detail.splitlines()[0]))} |"
        )
    lines.append("")
    head = "\n".join(lines) + "\n"
    sections = []
    for outcome in suite.scenarios:
        if outcome.result is None:
            continue
        body = render_markdown(outcome.result, marker=False, heading="####")
        sections.append(f"<details><summary>{cell(outcome.name)}</summary>\n\n{body}\n</details>\n")
    text = head + "\n".join(sections)
    if len(text) > max_chars:
        text = head + "\nPer-scenario details are in each scenario's report.\n"
    return text


def suite_document(suite: SuiteResult) -> dict[str, Any]:
    """The suite result without the embedded comparisons (those are written per scenario)."""
    return suite.model_dump(mode="json", exclude={"scenarios": {"__all__": {"result"}}})
