"""JUnit XML: one test case per gated metric, understood by most CI systems."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from physim_gate.comparison import ComparisonResult, MetricComparison, Verdict
from physim_gate.reporting.format import interval, num, signed, unit_suffix


def _details(metric: MetricComparison) -> str:
    lines = [f"verdict: {metric.verdict.value}", f"evidence: {metric.evidence.outcome.value}"]
    if metric.numerical is not None:
        n = metric.numerical
        u = unit_suffix(metric)
        lines += [
            f"baseline {metric.statistic.value}: {num(n.baseline)}{u}",
            f"candidate {metric.statistic.value}: {num(n.candidate)}{u}",
            f"degradation: {signed(n.degradation)}{u} (positive = worse)",
            f"allowed margin: {num(n.margin)}{u} [{n.margin_basis}]",
        ]
    if metric.evidence.degradation_ci is not None:
        lines.append(
            f"degradation interval ({num(metric.evidence.adjusted_confidence)}): "
            f"{interval(metric.evidence.degradation_ci)}"
        )
    lines += [f"- {reason}" for reason in metric.reasons]
    return "\n".join(lines)


def render_junit(result: ComparisonResult) -> str:
    suite_name = f"physim-gate.{result.candidate.scenario.id}"
    cases = []
    failures = errors = skipped = 0
    for metric in result.metrics:
        case = ET.Element("testcase", {"classname": suite_name, "name": metric.name, "time": "0"})
        message = metric.reasons[-1] if metric.reasons else metric.verdict.value
        details = _details(metric)
        if metric.verdict is Verdict.FAIL and metric.blocking:
            failures += 1
            node = ET.SubElement(case, "failure", {"message": message, "type": "regression"})
            node.text = details
        elif metric.verdict is Verdict.INVALID and metric.blocking:
            errors += 1
            node = ET.SubElement(case, "error", {"message": message, "type": "invalid"})
            node.text = details
        elif metric.verdict is Verdict.INCONCLUSIVE and metric.blocking:
            failures += 1
            node = ET.SubElement(case, "failure", {"message": message, "type": "inconclusive"})
            node.text = details
        elif metric.verdict is not Verdict.PASS:
            skipped += 1
            ET.SubElement(
                case, "skipped", {"message": f"non-blocking {metric.verdict.value}: {message}"}
            )
        out = ET.SubElement(case, "system-out")
        out.text = details
        cases.append(case)

    for issue in result.run_blocking_issues:
        errors += 1
        case = ET.Element(
            "testcase", {"classname": suite_name, "name": "run-compatibility", "time": "0"}
        )
        node = ET.SubElement(case, "error", {"message": issue, "type": "invalid"})
        node.text = issue
        cases.append(case)

    suite = ET.Element(
        "testsuite",
        {
            "name": suite_name,
            "tests": str(len(cases)),
            "failures": str(failures),
            "errors": str(errors),
            "skipped": str(skipped),
            "time": "0",
            "timestamp": result.generated_at.isoformat(),
        },
    )
    props = ET.SubElement(suite, "properties")
    for key, value in (
        ("verdict", result.verdict.value),
        ("summary", result.summary),
        ("baseline.run_id", result.baseline.run_id),
        ("candidate.run_id", result.candidate.run_id),
        ("candidate.source.commit", result.candidate.source.commit or ""),
        ("tool.version", result.tool.version),
    ):
        ET.SubElement(props, "property", {"name": key, "value": value})
    suite.extend(cases)
    root = ET.Element(
        "testsuites",
        {
            "name": "physim-gate",
            "tests": suite.get("tests", "0"),
            "failures": str(failures),
            "errors": str(errors),
            "skipped": str(skipped),
        },
    )
    root.append(suite)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
