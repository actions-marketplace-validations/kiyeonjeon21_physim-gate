"""Command-line interface: ``physim-gate``."""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
import yaml
from rich.markup import escape
from rich.text import Text

from physim_gate import __version__
from physim_gate.adapters.base import AdapterError, discover, get_adapter, parse_options
from physim_gate.comparison import (
    ComparisonResult,
    ExitCode,
    InputRef,
    Inputs,
    Verdict,
    load_comparison,
)
from physim_gate.config import GateConfig, load_config
from physim_gate.engine import compare_runs
from physim_gate.planning import Plan, PlanningError, plan_binary, plan_continuous
from physim_gate.provenance import sha256_path
from physim_gate.reporting.html import render_html
from physim_gate.reporting.junit import render_junit
from physim_gate.reporting.markdown import render_markdown
from physim_gate.reporting.terminal import VERDICT_STYLE, make_console, print_comparison
from physim_gate.schema import (
    FAILURE_RATE_METRIC,
    Direction,
    InputError,
    RunResult,
    SchemaError,
    dump_json,
)
from physim_gate.validation import lint_run

EXIT_CODES_HELP = """\
**Exit codes**

- **0** gate passed, or the command succeeded
- **1** regression detected (compare), or invalid input (validate)
- **2** usage error, unreadable input, invalid config or result document
- **3** no regression shown, but a blocking metric is inconclusive or invalid
- **4** internal error (please report it)
"""

app = typer.Typer(
    name="physim-gate",
    help="Test physical AI like software: catch behavioral regressions in simulation results.",
    epilog=EXIT_CODES_HELP,
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode="markdown",
    context_settings={"help_option_names": ["-h", "--help"]},
)

out = make_console()
err = make_console(stderr=True)


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


class ReportFormat(StrEnum):
    TEXT = "text"
    JSON = "json"
    MARKDOWN = "markdown"


class SchemaKind(StrEnum):
    RUN = "run"
    COMPARISON = "comparison"
    CONFIG = "config"


def _fail(message: str, code: ExitCode = ExitCode.USAGE) -> NoReturn:
    err.print(Text("error: ", style="bold red") + Text(message))
    raise typer.Exit(int(code))


@contextmanager
def _guard() -> Iterator[None]:
    """Map known errors to exit code 2 and anything unexpected to exit code 4."""
    try:
        yield
    except (typer.Exit, typer.Abort, SystemExit):
        raise
    except (SchemaError, AdapterError) as exc:
        _fail(str(exc))
    except Exception:
        err.print(
            Text("internal error: this is a bug in physim-gate, please report it", style="bold red")
        )
        err.print(traceback.format_exc(), markup=False)
        raise typer.Exit(int(ExitCode.INTERNAL)) from None


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"physim-gate {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show the version."
        ),
    ] = False,
) -> None:
    """Test physical AI like software: catch behavioral regressions in simulation results."""


AdapterOpt = Annotated[
    str,
    typer.Option(
        "--adapter", "-a", help="Adapter that reads the input files. See 'physim-gate adapters'."
    ),
]
AdapterOptionsOpt = Annotated[
    list[str] | None,
    typer.Option("--adapter-option", "-O", help="Adapter option as key=value. Repeatable."),
]
FormatOpt = Annotated[OutputFormat, typer.Option("--format", "-f", help="Output format on stdout.")]
ReportFormatOpt = Annotated[
    ReportFormat, typer.Option("--format", "-f", help="Output format on stdout.")
]
MarkdownOpt = Annotated[
    Path | None,
    typer.Option("--markdown", help="Write a Markdown summary here (for PR comments)."),
]


def _load(adapter_name: str, path: Path, options: dict[str, str]) -> RunResult:
    if not path.exists():
        _fail(f"{path}: no such file")
    return get_adapter(adapter_name).load(path, options)


def _write(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        _fail(f"{path}: cannot write ({exc.strerror or exc})")


@app.command(epilog=EXIT_CODES_HELP)
def compare(
    baseline: Annotated[Path, typer.Option("--baseline", "-b", help="Baseline run result.")],
    candidate: Annotated[Path, typer.Option("--candidate", "-c", help="Candidate run result.")],
    config: Annotated[Path, typer.Option("--config", "-g", help="Gate config (gate.yaml).")],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write the comparison result JSON here.")
    ] = None,
    junit: Annotated[Path | None, typer.Option("--junit", help="Write JUnit XML here.")] = None,
    html: Annotated[Path | None, typer.Option("--html", help="Write the HTML report here.")] = None,
    markdown: MarkdownOpt = None,
    adapter: AdapterOpt = "json",
    adapter_option: AdapterOptionsOpt = None,
    output_format: ReportFormatOpt = ReportFormat.TEXT,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Explain passing metrics too.")
    ] = False,
) -> None:
    """Compare a candidate run against a baseline and gate on regressions."""
    with _guard():
        options = parse_options(adapter_option or [])
        gate = load_config(config)
        base_run = _load(adapter, baseline, options)
        cand_run = _load(adapter, candidate, options)
        inputs = Inputs(
            baseline=InputRef(path=str(baseline), sha256=sha256_path(baseline)),
            candidate=InputRef(path=str(candidate), sha256=sha256_path(candidate)),
            config=InputRef(path=str(config), sha256=sha256_path(config)),
            adapter=adapter,
        )
        result = compare_runs(base_run, cand_run, gate, inputs=inputs)
        _emit(
            result,
            output=output,
            junit=junit,
            html=html,
            markdown=markdown,
            output_format=output_format,
            verbose=verbose,
        )
    raise typer.Exit(result.exit_code)


def _emit(
    result: ComparisonResult,
    *,
    output: Path | None,
    junit: Path | None,
    html: Path | None,
    markdown: Path | None,
    output_format: ReportFormat,
    verbose: bool,
) -> None:
    if output is not None:
        _write(output, dump_json(result))
    if junit is not None:
        _write(junit, render_junit(result))
    if html is not None:
        _write(html, render_html(result))
    if markdown is not None:
        _write(markdown, render_markdown(result))
    with _tolerate_closed_stdout():
        if output_format is ReportFormat.JSON:
            typer.echo(dump_json(result), nl=False)
            return
        if output_format is ReportFormat.MARKDOWN:
            typer.echo(render_markdown(result), nl=False)
            return
        print_comparison(result, out, verbose=verbose)
        written = [str(p) for p in (output, junit, html, markdown) if p is not None]
        if written:
            # Never break a path across lines: it could not be copied from the terminal.
            out.print(Text(f"wrote {', '.join(written)}", style="dim"), soft_wrap=True)


@contextmanager
def _tolerate_closed_stdout() -> Iterator[None]:
    """Keep the verdict's exit code when stdout is closed early (``... | head``).

    Without this, the BrokenPipeError would turn any verdict into exit code 1, which means
    "regression". Further writes go to /dev/null so the interpreter's final flush stays quiet.
    """
    try:
        yield
        sys.stdout.flush()
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)


@app.command(epilog=EXIT_CODES_HELP)
def report(
    comparison: Annotated[
        Path, typer.Argument(help="A comparison result written by 'compare --output'.")
    ],
    html: Annotated[Path | None, typer.Option("--html", help="Write the HTML report here.")] = None,
    junit: Annotated[Path | None, typer.Option("--junit", help="Write JUnit XML here.")] = None,
    markdown: MarkdownOpt = None,
    output_format: ReportFormatOpt = ReportFormat.TEXT,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Explain passing metrics too.")
    ] = False,
) -> None:
    """Render a saved comparison result. Exits with the comparison's original exit code."""
    with _guard():
        result = load_comparison(comparison)
        _emit(
            result,
            output=None,
            junit=junit,
            html=html,
            markdown=markdown,
            output_format=output_format,
            verbose=verbose,
        )
    raise typer.Exit(result.exit_code)


@app.command(epilog=EXIT_CODES_HELP)
def validate(
    results: Annotated[Path, typer.Argument(help="Run result file to check.")],
    adapter: AdapterOpt = "json",
    adapter_option: AdapterOptionsOpt = None,
    strict: Annotated[bool, typer.Option("--strict", help="Treat warnings as errors.")] = False,
    output_format: FormatOpt = OutputFormat.TEXT,
) -> None:
    """Check that a run result is valid and flag suspicious content."""
    errors: list[str] = []
    warnings: list[dict[str, str]] = []
    run: RunResult | None = None
    with _guard():
        options = parse_options(adapter_option or [])
        if not results.exists():
            _fail(f"{results}: no such file")
        try:
            run = get_adapter(adapter).load(results, options)
        except InputError:
            raise
        except SchemaError as exc:
            errors = [exc.message, *exc.problems]
        if run is not None:
            warnings = [i.model_dump() for i in lint_run(run)]

    valid = not errors and not (strict and warnings)
    if output_format is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                {
                    "path": str(results),
                    "valid": valid,
                    "errors": errors,
                    "warnings": warnings,
                    "run_id": run.run_id if run else None,
                    "trials": len(run.trials) if run else None,
                    "metrics": sorted(run.metrics) if run else [],
                },
                indent=2,
            )
        )
    else:
        if errors:
            err.print(Text("invalid: ", style="bold red") + Text(errors[0]))
            for problem in errors[1:]:
                err.print(f"  - {escape(problem)}")
        elif run is not None:
            out.print(
                Text("valid: ", style="bold green")
                + Text(
                    f"{results} (run {run.run_id}, {len(run.trials)} trials, "
                    f"metrics: {', '.join(run.metrics) or 'none'})"
                )
            )
        for w in warnings:
            out.print(Text("warning: ", style="yellow") + Text(f"{w['location']}: {w['message']}"))
    raise typer.Exit(int(ExitCode.OK if valid else ExitCode.REGRESSION))


_INIT_HEADER = """\
# PhySim Gate configuration. Reference: docs/comparison-semantics.md
#
# For each gated metric, set how much degradation is acceptable:
#   max_absolute_degradation: in the metric's own unit
#   max_relative_degradation: as a fraction of the baseline value (0.05 = 5%)
# If both are set, the stricter one applies. "Degradation" already accounts for
# direction (higher_is_better / lower_is_better), so margins are always >= 0.
#
# A metric FAILs only when the whole confidence interval of the degradation lies
# beyond the margin, and PASSes only when it lies entirely within the margin.
# Anything in between is INCONCLUSIVE, which blocks by default (policy.on_inconclusive).
"""


def _init_document(run: RunResult | None) -> dict[str, Any]:
    metrics: dict[str, dict[str, Any]] = {}
    if run is None:
        metrics = {
            "success": {"max_absolute_degradation": 0.05},
            "episode_time": {"max_relative_degradation": 0.10},
            FAILURE_RATE_METRIC: {"max_absolute_degradation": 0.02},
        }
    else:
        for name, spec in run.metrics.items():
            values = [v for v in run.metric_values(name).values() if v is not None]
            binary = bool(values) and all(v in (0.0, 1.0) for v in values)
            mean = sum(values) / len(values) if values else 0.0
            if binary or mean == 0:
                metrics[name] = {"max_absolute_degradation": 0.05 if binary else 0.0}
            else:
                metrics[name] = {"max_relative_degradation": 0.05}
            metrics[name]["direction"] = spec.direction.value
        metrics[FAILURE_RATE_METRIC] = {
            "max_absolute_degradation": 0.02,
            "direction": Direction.LOWER_IS_BETTER.value,
        }
    document: dict[str, Any] = {
        "version": 1,
        "defaults": {"confidence": 0.95, "min_samples": 10, "pairing": "auto", "statistic": "mean"},
        "policy": {"on_inconclusive": "fail", "on_invalid": "fail"},
        "metrics": metrics,
    }
    GateConfig.model_validate(document)
    return document


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="Where to write the config.")] = Path("gate.yaml"),
    from_results: Annotated[
        Path | None,
        typer.Option("--from", help="Derive the metric list from an existing run result."),
    ] = None,
    adapter: AdapterOpt = "json",
    adapter_option: AdapterOptionsOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    """Write a starter gate.yaml. Review every margin before relying on it."""
    with _guard():
        if path.exists() and not force:
            _fail(f"{path} already exists (use --force to overwrite)")
        options = parse_options(adapter_option or [])
        run = _load(adapter, from_results, options) if from_results is not None else None
        document = _init_document(run)
        body = yaml.safe_dump(document, sort_keys=False, default_flow_style=False)
        note = (
            f"# Metrics were taken from {from_results}. The margins are placeholders.\n\n"
            if from_results
            else "# The metric names below are examples. Replace them with your own.\n\n"
        )
        _write(path, _INIT_HEADER + note + body)
    out.print(
        Text("wrote ", style="green")
        + Text(str(path))
        + Text(" - review each margin before gating on it", style="dim")
    )


def _percent(value: float) -> str:
    if 0 < value < 0.001:
        return "<0.1%"
    if 0.999 < value < 1:
        return ">99.9%"
    return f"{value * 100:.1f}%"


def _print_plan(plan: Plan, rate: float | None, direction: Direction) -> None:
    rows: list[tuple[str, str]] = []
    if plan.kind == "binary" and rate is not None:
        bad = direction is Direction.LOWER_IS_BETTER
        candidate = rate + plan.true_degradation if bad else rate - plan.true_degradation
        what = "0/1 metric, " + ("lower is better" if bad else "higher is better")
        rows += [("baseline rate", f"{rate:g}"), ("candidate rate", f"{candidate:g}")]
    else:
        what = "continuous metric"
    rows += [
        ("true degradation", f"{plan.true_degradation:g}"),
        ("margin", f"{plan.margin:g}"),
        (
            "goal",
            f"{plan.goal.value.upper()} with probability >= {plan.power:.0%}, "
            f"{plan.confidence:.0%} intervals",
        ),
        ("method", plan.method),
    ]
    out.print(Text(f"plan for a {what}", style="bold"))
    for label, value in rows:
        out.print(Text(f"  {label:<18}", style="dim") + Text(value))
    out.print()
    if plan.trials is None:
        out.print(
            Text(f"  more than {plan.max_trials:,} trials per run", style="bold yellow")
            + Text(" - widen the margin or accept INCONCLUSIVE verdicts", style="dim")
        )
    else:
        out.print(Text(f"  {plan.trials:,} trials per run", style="bold"))
    if plan.probabilities is not None:
        p = plan.probabilities
        line = Text("  verdicts at that size: ", style="dim")
        line.append(f"PASS {_percent(p.pass_)}", style=VERDICT_STYLE[Verdict.PASS])
        line.append("  ")
        line.append(
            f"INCONCLUSIVE {_percent(p.inconclusive)}", style=VERDICT_STYLE[Verdict.INCONCLUSIVE]
        )
        line.append("  ")
        line.append(f"FAIL {_percent(p.fail)}", style=VERDICT_STYLE[Verdict.FAIL])
        out.print(line)
    out.print()
    for note in plan.notes:
        out.print(Text(f"  note: {note}", style="dim"))


@app.command(epilog=EXIT_CODES_HELP)
def plan(
    margin: Annotated[
        float,
        typer.Option(
            "--margin", "-m", help="Allowed degradation, in the metric's unit (absolute)."
        ),
    ],
    rate: Annotated[
        float | None,
        typer.Option("--rate", "-r", help="0/1 metric: expected baseline rate of 1s (0-1)."),
    ] = None,
    stdev: Annotated[
        float | None,
        typer.Option(
            "--stdev",
            "-s",
            help="Continuous metric: standard deviation of one trial's value "
            "(of the paired differences with --paired).",
        ),
    ] = None,
    direction: Annotated[
        Direction,
        typer.Option(
            "--direction",
            "-d",
            metavar="DIRECTION",
            help="0/1 metric: higher_is_better (a success rate) or lower_is_better "
            "(a collision rate).",
        ),
    ] = Direction.HIGHER_IS_BETTER,
    true_degradation: Annotated[
        float,
        typer.Option(
            "--true-degradation",
            "-t",
            help="Degradation you expect the candidate to really have. Below the margin the "
            "goal is PASS, above it FAIL.",
        ),
    ] = 0.0,
    paired: Annotated[
        bool, typer.Option("--paired", help="Continuous metric: runs share trial conditions.")
    ] = False,
    confidence: Annotated[float, typer.Option("--confidence", help="Interval level.")] = 0.95,
    power: Annotated[
        float, typer.Option("--power", help="Wanted probability of the goal verdict.")
    ] = 0.8,
    min_samples: Annotated[int, typer.Option("--min-samples", help="The gate's min_samples.")] = 10,
    output_format: FormatOpt = OutputFormat.TEXT,
) -> None:
    """Estimate how many trials per run a metric needs to reach a verdict."""
    if (rate is None) == (stdev is None):
        _fail("give exactly one of --rate (0/1 metric) or --stdev (continuous metric)")
    try:
        if rate is not None:
            if paired:
                _fail("--paired applies to --stdev only; the 0/1 plan is exact for unpaired runs")
            result = plan_binary(
                rate,
                margin,
                direction=direction,
                true_degradation=true_degradation,
                confidence=confidence,
                power=power,
                min_samples=min_samples,
            )
        else:
            assert stdev is not None
            result = plan_continuous(
                stdev,
                margin,
                paired=paired,
                true_degradation=true_degradation,
                confidence=confidence,
                power=power,
                min_samples=min_samples,
            )
    except PlanningError as exc:
        _fail(str(exc))
    if output_format is OutputFormat.JSON:
        document = dataclasses.asdict(result)
        if result.probabilities is not None:
            document["probabilities"] = {
                "pass": result.probabilities.pass_,
                "inconclusive": result.probabilities.inconclusive,
                "fail": result.probabilities.fail,
            }
        typer.echo(json.dumps(document, indent=2))
        return
    _print_plan(result, rate, direction)


@app.command()
def demo(
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated runs and reports.")
    ] = Path("physim-gate-demo"),
) -> None:
    """Generate synthetic runs and gate them with the real engine (no simulator needed)."""
    from physim_gate.demo import DemoOutcome, run_demo

    def show(outcome: DemoOutcome) -> None:
        scenario = outcome.scenario
        out.rule(Text(f"{scenario.name}: {scenario.title}", style="bold"), align="left")
        print_comparison(outcome.result, out)
        out.print(
            Text(
                f"reports: {outcome.directory}/report.html, junit.xml, comparison.json", style="dim"
            )
        )
        out.print()

    with _guard():
        outcomes = run_demo(output_dir, on_result=show)

    out.rule(Text("demo summary", style="bold"), align="left")
    for outcome in outcomes:
        mark = (
            "as expected"
            if outcome.as_expected
            else f"UNEXPECTED (expected {outcome.scenario.expected.value})"
        )
        line = Text(f"  {outcome.scenario.name:<24}")
        line.append(
            f"{outcome.result.verdict.value:<13}", style=VERDICT_STYLE[outcome.result.verdict]
        )
        line.append(
            f"exit {outcome.result.exit_code}  {mark}",
            style="dim" if outcome.as_expected else "bold red",
        )
        out.print(line)
    out.print()
    out.print(f"Open {output_dir / 'regression' / 'report.html'} to see a failing report.")
    out.print(
        "Re-run any comparison: physim-gate compare "
        f"-b {output_dir}/regression/baseline.json -c {output_dir}/regression/candidate.json "
        f"-g {output_dir}/regression/gate.yaml"
    )
    if not all(o.as_expected for o in outcomes):
        _fail("a demo scenario produced an unexpected verdict", ExitCode.INTERNAL)


@app.command()
def adapters(output_format: FormatOpt = OutputFormat.TEXT) -> None:
    """List available adapters, including third-party plugins."""
    with _guard():
        found = discover()
    if output_format is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                [
                    {"name": i.name, "target": i.target, "origin": i.origin, "error": i.error}
                    for i in found.values()
                ],
                indent=2,
            )
        )
        return
    for info in found.values():
        line = Text(f"{info.name:<12}", style="bold")
        if info.error:
            line.append(f"{info.target} ({info.origin}): {info.error}", style="red")
        else:
            description = _describe(info.name)
            line.append(f"{description}  ", style="")
            line.append(f"[{info.origin}]", style="dim")
        out.print(line)


def _describe(name: str) -> str:
    try:
        return get_adapter(name).description
    except AdapterError as exc:
        return f"unavailable: {exc}"


_SCHEMAS: dict[SchemaKind, Callable[[], dict[str, Any]]] = {
    SchemaKind.RUN: RunResult.model_json_schema,
    SchemaKind.COMPARISON: ComparisonResult.model_json_schema,
    SchemaKind.CONFIG: GateConfig.model_json_schema,
}


@app.command()
def schema(
    kind: Annotated[SchemaKind, typer.Argument(help="Which document.")] = SchemaKind.RUN,
) -> None:
    """Print the JSON Schema of a document type."""
    typer.echo(json.dumps(_SCHEMAS[kind](), indent=2))


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)
