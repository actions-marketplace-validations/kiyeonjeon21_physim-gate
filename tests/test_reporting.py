from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest
from rich.console import Console

from physim_gate.comparison import ComparisonResult, comparison_from_data
from physim_gate.engine import compare_runs
from physim_gate.reporting import print_comparison, render_html, render_junit, render_markdown
from physim_gate.reporting.markdown import MARKER
from physim_gate.schema import SchemaError
from tests.conftest import make_config, make_run

N = 30


def result() -> ComparisonResult:
    good = [float(i % 5) for i in range(N)]
    base = make_run(
        {"speed": good, "crash": [0.0] * N, "noisy": good, "gone": good},
        run_id="base",
        directions={"crash": "lower_is_better"},
        units={"speed": "m/s"},
        source={"commit": "a" * 40},
    )
    cand = make_run(
        {
            "speed": [v - 2 for v in good],
            "crash": [1.0] * 5 + [0.0] * (N - 5),
            "noisy": good[::-1],
            "gone": [None] * N,
        },
        run_id="cand <script>",
        directions={"crash": "lower_is_better"},
        units={"speed": "m/s"},
    )
    config = make_config(
        {
            "speed": {"max_absolute_degradation": 0.5},
            "crash": {"max_absolute_degradation": 0.01, "required": False},
            "noisy": {"max_absolute_degradation": 0.1, "pairing": "unpaired"},
            "gone": {"max_absolute_degradation": 0.1},
        }
    )
    return compare_runs(base, cand, config)


def test_junit_structure() -> None:
    root = ET.fromstring(render_junit(result()))
    assert root.tag == "testsuites"
    suite = root.find("testsuite")
    assert suite is not None
    cases = {c.get("name"): c for c in suite.findall("testcase")}
    assert set(cases) == {"speed", "crash", "noisy", "gone"}
    assert cases["speed"].find("failure") is not None
    assert cases["crash"].find("skipped") is not None  # optional metric
    assert cases["gone"].find("error") is not None
    noisy = cases["noisy"].find("failure")
    assert noisy is not None
    assert noisy.get("type") == "inconclusive"
    assert (
        suite.get("tests"),
        suite.get("failures"),
        suite.get("errors"),
        suite.get("skipped"),
    ) == ("4", "2", "1", "1")
    props = {p.get("name"): p.get("value") for p in suite.iter("property")}
    assert props["verdict"] == "FAIL"


def test_html_is_self_contained_and_escaped() -> None:
    html = render_html(result())
    assert html.startswith("<!doctype html>")
    # No external resources: no src/href pointing off-page, no imports.
    assert not re.search(r'(src|href)="(https?:)?//', html)
    assert "@import" not in html
    assert "cand <script>" not in html
    assert "cand &lt;script&gt;" in html
    for name in ("speed", "crash", "noisy", "gone"):
        assert f'id="metric-{name}"' in html
    assert "prefers-color-scheme:dark" in html
    assert "Share of trials with value 1" in html  # binary metric chart
    assert "Observed values" in html
    assert "Degradation vs allowed margin" in html
    assert "- · statistic" not in html
    assert "None resamples" not in html
    assert "paired score interval (Newcombe)" in html  # the binary "crash" metric
    assert "paired bootstrap, 10000 resamples" in html


def test_markdown_summary() -> None:
    text = render_markdown(result())
    assert text.startswith(MARKER + "\n### ❌ PhySim Gate: FAIL\n\nRegression detected in ")
    rows = [line for line in text.splitlines() if line.startswith("| `")]
    assert len(rows) == 4
    assert rows[0].startswith("| `speed` ↑ (m/s) | 2 | 0 | +2 |")
    assert rows[0].endswith("| 30 pairs | ❌ FAIL |")
    assert rows[1].endswith("⚠️ INCONCLUSIVE (non-blocking) |") or "(non-blocking)" in rows[1]
    assert "`gone`" in rows[3]
    assert rows[3].endswith("| ⛔ INVALID |")
    # Untrusted strings only appear inside code spans, which GitHub renders literally.
    assert "`cand <script>`" in text
    assert "<script>" not in text.replace("`cand <script>`", "")
    assert "<details><summary>" in text
    assert "**speed**: ❌ FAIL" in text
    assert "speed**: ✅" not in text


def test_markdown_escapes_table_breaking_names() -> None:
    values = [float(i % 5) for i in range(N)]
    base = make_run({"m": values}, run_id="x|y`z", units={"m": "a|b"})
    cand = make_run({"m": values}, run_id="c", units={"m": "a|b"})
    text = render_markdown(
        compare_runs(base, cand, make_config({"m": {"max_absolute_degradation": 1}}))
    )
    assert "`x\\|y'z`" in text
    row = next(line for line in text.splitlines() if line.startswith("| `m`"))
    assert row.startswith("| `m` ↑ (a\\|b) |")
    assert row.replace("\\|", "").count("|") == 9


def test_markdown_degrades_to_fit_comment_limits() -> None:
    full = render_markdown(result())
    without_reasons = render_markdown(result(), max_chars=len(full) - 1)
    assert "<details>" not in without_reasons
    assert without_reasons.count("| `") == 4
    folded = render_markdown(result(), max_chars=100)
    assert "<details>" not in folded
    assert "passing metric(s) not listed" not in folded  # no metric passed in this result


def test_terminal_table_and_narrow_layout() -> None:
    wide = Console(width=140, record=True, color_system=None)
    print_comparison(result(), wide)
    text = wide.export_text()
    assert "FAIL" in text
    assert "speed ↑" in text
    assert "interval" in text
    narrow = Console(width=70, record=True, color_system=None)
    print_comparison(result(), narrow)
    text = narrow.export_text()
    assert "FAIL  speed ↑" in text
    assert "degradation +2 m/s" in text


def test_comparison_round_trip_and_rejections() -> None:
    original = result()
    data = original.model_dump(mode="json")
    assert comparison_from_data(data) == original
    with pytest.raises(SchemaError, match="unsupported comparison schema version"):
        comparison_from_data({**data, "schema_version": "2.0"})
    with pytest.raises(SchemaError, match="not a comparison result"):
        comparison_from_data({"run_id": "x"})


def test_run_level_issues_are_reported_everywhere() -> None:
    values = [float(i % 5) for i in range(N)]
    base = make_run({"m": values}, run_id="b", scenario="left_turn")
    cand = make_run({"m": values}, run_id="c", scenario="right_turn", status="partial")
    result = compare_runs(base, cand, make_config({"m": {"max_absolute_degradation": 1}}))
    assert result.verdict.value == "INVALID"

    console = Console(width=140, record=True, color_system=None)
    print_comparison(result, console)
    text = console.export_text()
    assert "[blocking] scenario differs" in text
    assert "candidate run status is 'partial'" in text

    suite = ET.fromstring(render_junit(result)).find("testsuite")
    assert suite is not None
    compat = [c for c in suite.findall("testcase") if c.get("name") == "run-compatibility"]
    assert len(compat) == 1
    assert compat[0].find("error") is not None

    html = render_html(result)
    assert "Run notes" in html
    assert "scenario differs" in html

    markdown = render_markdown(result)
    assert "**Run notes**" in markdown
    assert "- **blocking:** scenario differs" in markdown
    assert "- candidate run status is 'partial'" in markdown


def test_markdown_folds_passing_metrics_last() -> None:
    values = [float(i % 5) for i in range(N)]
    base = make_run({"m": values, "k": values}, run_id="b")
    cand = make_run({"m": values, "k": [None] * N}, run_id="c")
    result = compare_runs(
        base,
        cand,
        make_config({"m": {"max_absolute_degradation": 1}, "k": {"max_absolute_degradation": 1}}),
    )
    folded = render_markdown(result, max_chars=100)
    assert "1 passing metric(s) not listed; see the HTML report." in folded
    assert "| `k`" in folded
    assert "| `m`" not in folded
