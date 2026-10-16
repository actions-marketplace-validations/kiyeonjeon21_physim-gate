"""Exercises the example third-party adapter through real entry-point discovery.

Runs only when ``examples/csv-adapter`` is installed (the CI 'extras' job does this).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("physim_gate_csv")

from physim_gate.adapters.base import AdapterError, discover, get_adapter
from physim_gate.config import load_config
from physim_gate.engine import compare_runs

DATA = Path(__file__).parent.parent / "examples" / "csv-adapter" / "data"
OPTIONS = {"directions": "success:higher,time_to_goal:lower", "scenario": "warehouse_nav"}


def test_plugin_is_discovered_from_its_distribution() -> None:
    info = discover()["csv"]
    assert info.origin == "physim-gate-csv"
    assert info.error is None


def test_plugin_runs_through_the_engine() -> None:
    adapter = get_adapter("csv")
    baseline = adapter.load(DATA / "baseline.csv", OPTIONS)
    candidate = adapter.load(DATA / "candidate.csv", OPTIONS)
    result = compare_runs(baseline, candidate, load_config(DATA / "gate.yaml"))
    assert result.verdict.value == "PASS"


def test_plugin_errors_surface_as_adapter_errors() -> None:
    with pytest.raises(AdapterError, match="give a direction"):
        get_adapter("csv").load(DATA / "baseline.csv", {})
