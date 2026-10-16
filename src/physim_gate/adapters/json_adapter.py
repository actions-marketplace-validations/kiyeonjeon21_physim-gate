"""Reads results that are already in the PhySim Gate run schema."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from physim_gate.adapters.base import reject_unknown_options
from physim_gate.schema import RunResult, load_run


class JsonAdapter:
    name = "json"
    description = "PhySim Gate run-result JSON (schema 1.x). The default."

    def load(self, source: Path, options: Mapping[str, str]) -> RunResult:
        reject_unknown_options(self.name, options, set())
        return load_run(source)
