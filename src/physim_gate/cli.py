"""Command-line interface: ``physim-gate``."""

from __future__ import annotations

import json
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
    load_comparison,
)
from physim_gate.config import GateConfig, load_config
from physim_gate.engine import compare_runs
from physim_gate.provenance import sha256_file
from physim_gate.reporting.html import render_html
from physim_gate.reporting.junit import render_junit
from physim_gate.reporting.terminal import VERDICT_STYLE, make_console, print_comparison
from physim_gate.schema import (
    FAILURE_RATE_METRIC,
    Direction,
    RunResult,
    SchemaError,
    dump_json,
)
from physim_gate.validation import lint_run

EXIT_CODES_HELP = """\
Exit codes:\n
  0  gate passed / command succeeded\n
  1  regression detected (compare) or invalid input (validate)\n
  2  usage error, unreadable input, invalid config or result document\n
  3  no regression shown, but a blocking metric is inconclusive or invalid\n
  4  internal error (please report it)
"""

app = typer.Typer(
    name="physim-gate",
    help="Test physical AI like software: catch behavioral regressions in simulation results.",
    epilog=EXIT_CODES_HELP,
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)

out = make_console()
err = make_console(stderr=True)


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


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
    adapter: AdapterOpt = "json",
    adapter_option: AdapterOptionsOpt = None,
    output_format: FormatOpt = OutputFormat.TEXT,
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
            baseline=InputRef(path=str(baseline), sha256=sha256_file(baseline)),
            candidate=InputRef(path=str(candidate), sha256=sha256_file(candidate)),
            config=InputRef(path=str(config), sha256=sha256_file(config)),
            adapter=adapter,
        )
        result = compare_runs(base_run, cand_run, gate, inputs=inputs)
        _emit(
            result,
            output=output,
            junit=junit,
            html=html,
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
    output_format: OutputFormat,
    verbose: bool,
) -> None:
    if output is not None:
        _write(output, dump_json(result))
    if junit is not None:
        _write(junit, render_junit(result))
    if html is not None:
        _write(html, render_html(result))
    if output_format is OutputFormat.JSON:
        typer.echo(dump_json(result), nl=False)
        return
    print_comparison(result, out, verbose=verbose)
    written = [str(p) for p in (output, junit, html) if p is not None]
    if written:
        out.print(Text(f"wrote {', '.join(written)}", style="dim"))


@app.command(epilog=EXIT_CODES_HELP)
def report(
    comparison: Annotated[
        Path, typer.Argument(help="A comparison result written by 'compare --output'.")
    ],
    html: Annotated[Path | None, typer.Option("--html", help="Write the HTML report here.")] = None,
    junit: Annotated[Path | None, typer.Option("--junit", help="Write JUnit XML here.")] = None,
    output_format: FormatOpt = OutputFormat.TEXT,
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
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    """Write a starter gate.yaml. Review every margin before relying on it."""
    with _guard():
        if path.exists() and not force:
            _fail(f"{path} already exists (use --force to overwrite)")
        run = _load(adapter, from_results, {}) if from_results is not None else None
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
