"""Example third-party adapter for PhySim Gate.

Reads a CSV file with one row per trial. Columns:

* ``trial_id`` (required) and ``seed`` (optional),
* ``status`` (optional, defaults to ``ok``),
* every other column is a metric. Empty cells are missing values.

Metric directions come from a ``directions`` option, for example
``-O directions=success:higher,time:lower``, because a CSV header cannot carry them.
Scenario and simulator names come from the ``scenario`` and ``simulator`` options.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from pathlib import Path

from physim_gate.adapters import AdapterError
from physim_gate.schema import Direction, RunResult, run_from_data

__all__ = ["CsvAdapter"]

_DIRECTIONS = {"higher": Direction.HIGHER_IS_BETTER, "lower": Direction.LOWER_IS_BETTER}
_KNOWN = {"directions", "scenario", "simulator", "run_id", "units"}


def _pairs(text: str) -> dict[str, str]:
    pairs = {}
    for item in filter(None, (part.strip() for part in text.split(","))):
        key, sep, value = item.partition(":")
        if not sep:
            raise AdapterError(f"expected name:value, got {item!r}")
        pairs[key.strip()] = value.strip()
    return pairs


class CsvAdapter:
    name = "csv"
    description = "One CSV row per trial (example third-party plugin)."

    def load(self, source: Path, options: Mapping[str, str]) -> RunResult:
        unknown = set(options) - _KNOWN
        if unknown:
            raise AdapterError(f"csv adapter: unknown option(s) {', '.join(sorted(unknown))}")
        directions = _pairs(options.get("directions", ""))
        units = _pairs(options.get("units", ""))
        try:
            with source.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            raise AdapterError(f"{source}: cannot read ({exc})") from exc
        if not rows:
            raise AdapterError(f"{source}: no rows")

        metric_names = [c for c in rows[0] if c not in {"trial_id", "seed", "status"}]
        missing = [m for m in metric_names if m not in directions]
        if missing:
            raise AdapterError(
                f"csv adapter: give a direction for {', '.join(missing)} "
                "(-O directions=name:higher,other:lower)"
            )
        metrics = {}
        for name in metric_names:
            if directions[name] not in _DIRECTIONS:
                raise AdapterError(f"direction for {name} must be 'higher' or 'lower'")
            metrics[name] = {
                "direction": _DIRECTIONS[directions[name]],
                "unit": units.get(name, ""),
            }

        trials = []
        for row in rows:
            values: dict[str, float | None] = {}
            for name in metric_names:
                cell = (row.get(name) or "").strip()
                try:
                    values[name] = float(cell) if cell else None
                except ValueError as exc:
                    raise AdapterError(f"{source}: {name}={cell!r} is not a number") from exc
            trial: dict[str, object] = {"trial_id": row["trial_id"], "metrics": values}
            if row.get("seed"):
                trial["seed"] = int(row["seed"])
            if row.get("status"):
                trial["status"] = row["status"]
                if row["status"] != "ok":
                    trial["failure"] = {"kind": row["status"]}
            trials.append(trial)

        return run_from_data(
            {
                "run_id": options.get("run_id", source.stem),
                "simulator": {"name": options.get("simulator", "unknown")},
                "scenario": {"id": options.get("scenario", "default")},
                "metrics": metrics,
                "trials": trials,
            },
            str(source),
        )
