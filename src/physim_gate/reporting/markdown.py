"""GitHub-flavored Markdown summary, sized for a pull-request comment or a job summary.

The first line is a hidden marker so a workflow can find and update its previous comment
instead of adding a new one on every push. GitHub rejects comments over 65,536 characters, so
the output degrades in steps when a comparison has many metrics: first the per-metric reasons
are dropped, then passing metrics are folded into a count.
"""

from __future__ import annotations

from collections.abc import Iterable

from physim_gate.comparison import ComparisonResult, MetricComparison, RunSummary, Verdict
from physim_gate.reporting.format import interval, method_label, num, signed

MARKER = "<!-- physim-gate -->"
#: GitHub's limit for comment bodies is 65,536 characters; leave room for surrounding text.
MAX_CHARS = 60_000

_ICON = {
    Verdict.PASS: "✅",
    Verdict.FAIL: "❌",
    Verdict.INCONCLUSIVE: "⚠️",
    Verdict.INVALID: "⛔",
}


def _cell(text: str) -> str:
    """Escape what would break a table cell or start Markdown formatting."""
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", " ")
    )


def _code(text: str) -> str:
    """An inline code span. GitHub renders its content literally, except that a pipe still
    ends a table cell unless escaped."""
    quoted = text.replace("`", "'").replace("|", "\\|").replace("\n", " ")
    return f"`{quoted}`"


def _verdict(metric: MetricComparison) -> str:
    label = f"{_ICON[metric.verdict]} {metric.verdict.value}"
    if metric.verdict is not Verdict.PASS and not metric.blocking:
        label += " (non-blocking)"
    return label


def _metric_name(metric: MetricComparison) -> str:
    arrow = ""
    if metric.direction is not None:
        arrow = " ↑" if metric.direction.value == "higher_is_better" else " ↓"
    unit = f" ({_cell(metric.unit)})" if metric.unit else ""
    optional = " optional" if not metric.required else ""
    return f"{_code(metric.name)}{arrow}{unit}{optional}"


def _count(metric: MetricComparison) -> str:
    if metric.pairing is not None and metric.pairing.mode == "paired":
        return f"{metric.pairing.n_pairs} pairs"
    if metric.baseline is not None and metric.candidate is not None:
        return f"{metric.baseline.n}/{metric.candidate.n}"
    return "-"


def _row(metric: MetricComparison) -> str:
    n = metric.numerical
    cells = [
        _metric_name(metric),
        num(n.baseline) if n else "-",
        num(n.candidate) if n else "-",
        signed(n.degradation) if n else "-",
        interval(metric.evidence.degradation_ci),
        num(n.margin) if n else "-",
        _count(metric),
        _verdict(metric),
    ]
    return "| " + " | ".join(cells) + " |"


def _run(label: str, run: RunSummary) -> str:
    parts = [f"{run.n_trials} trials"]
    if run.n_trials_not_ok:
        parts.append(f"{run.n_trials_not_ok} not ok")
    if run.policy is not None:
        version = f"@{run.policy.version}" if run.policy.version else ""
        parts.append(f"policy {_code(run.policy.id + version)}")
    if run.source.commit:
        parts.append(
            f"commit {_code(run.source.commit[:12])}" + (" (dirty)" if run.source.dirty else "")
        )
    return f"**{label}** {_code(run.run_id)}: " + ", ".join(parts)


def _simulator(run: RunSummary) -> str:
    return run.simulator.name + (f" {run.simulator.version}" if run.simulator.version else "")


def _details(metrics: Iterable[MetricComparison]) -> list[str]:
    lines: list[str] = []
    for metric in metrics:
        lines.append(f"**{_cell(metric.name)}**: {_verdict(metric)}")
        method = method_label(metric)
        if method is not None:
            lines.append(f"- method: {method}")
        lines += [f"- {_cell(reason)}" for reason in metric.reasons]
        lines.append("")
    return lines


def _render(result: ComparisonResult, *, reasons: bool, fold_passing: bool) -> str:
    sims = sorted({_simulator(result.baseline), _simulator(result.candidate)})
    lines = [
        MARKER,
        f"### {_ICON[result.verdict]} PhySim Gate: {result.verdict.value}",
        "",
        _cell(result.summary[:1].upper() + result.summary[1:]),
        "",
        f"Scenario {_code(result.candidate.scenario.id)} ({result.candidate.domain.value}), "
        f"simulator {', '.join(_code(s) for s in sims)}.  ",
        _run("Baseline", result.baseline) + "  ",
        _run("Candidate", result.candidate),
        "",
    ]
    shown = list(result.metrics)
    folded = 0
    if fold_passing:
        shown = [m for m in result.metrics if m.verdict is not Verdict.PASS]
        folded = len(result.metrics) - len(shown)
    if shown:
        lines += [
            "| Metric | Baseline | Candidate | Degradation | Interval | Margin | n | Verdict |",
            "|---|--:|--:|--:|--:|--:|--:|---|",
            *(_row(m) for m in shown),
            "",
        ]
    if folded:
        lines += [f"{folded} passing metric(s) not listed; see the HTML report.", ""]
    lines.append(
        "<sub>Degradation > 0 means worse. Interval: confidence interval of the degradation. "
        "n: matched pairs, or baseline/candidate observations.</sub>"
    )
    lines.append("")

    issues = [f"- **blocking:** {_cell(i)}" for i in result.run_blocking_issues]
    issues += [f"- {_cell(i)}" for i in result.run_issues]
    if issues:
        lines += ["**Run notes**", "", *issues, ""]

    explained = [m for m in result.metrics if m.verdict is not Verdict.PASS]
    if reasons and (explained or result.metrics):
        lines += [
            "<details><summary>Why each metric got its verdict</summary>",
            "",
            *_details(explained or result.metrics),
            "</details>",
            "",
        ]
    if result.untracked_metrics:
        lines += [f"Not gated: {', '.join(_code(m) for m in result.untracked_metrics)}", ""]
    lines.append(f"<sub>physim-gate {result.tool.version}, exit code {result.exit_code}</sub>")
    return "\n".join(lines) + "\n"


def render_markdown(result: ComparisonResult, *, max_chars: int = MAX_CHARS) -> str:
    """Render the comparison as Markdown no longer than ``max_chars`` where possible."""
    text = _render(result, reasons=True, fold_passing=False)
    if len(text) > max_chars:
        text = _render(result, reasons=False, fold_passing=False)
    if len(text) > max_chars:
        text = _render(result, reasons=False, fold_passing=True)
    return text
