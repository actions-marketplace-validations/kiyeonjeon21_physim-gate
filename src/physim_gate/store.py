"""A file-based baseline store: one normalized run result per scenario and name.

Baselines live in ``<store>/<scenario>/<name>.json``, where ``<store>`` defaults to
``.physim-gate/baselines`` in the working directory (or ``$PHYSIM_GATE_STORE``). The files are
ordinary run-result JSON, so they can be committed, reviewed and diffed like any other file.
The name is typically a branch (``main``) or a release (``v2.1``).

A comparison refers to a stored baseline as ``@<name>``; the scenario comes from the candidate
run, so one reference works for every scenario in a suite.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from physim_gate.provenance import sha256_path
from physim_gate.schema import RunResult, SchemaError, load_run, write_run

DEFAULT_STORE = Path(".physim-gate") / "baselines"
STORE_ENV = "PHYSIM_GATE_STORE"
REFERENCE_PREFIX = "@"

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


class StoreError(SchemaError):
    """A baseline could not be stored or found."""


def store_root(store: Path | None = None) -> Path:
    if store is not None:
        return store
    configured = os.environ.get(STORE_ENV)
    return Path(configured) if configured else DEFAULT_STORE


def check_name(name: str) -> str:
    if not _NAME.match(name) or name in (".", ".."):
        raise StoreError(
            f"invalid baseline name {name!r}; use letters, digits, '.', '_' and '-', "
            "starting with a letter or digit"
        )
    return name


def scenario_directory(scenario: str) -> str:
    """A directory name for a scenario id. Unsafe characters become '_'."""
    safe = _UNSAFE.sub("_", scenario).strip(".")
    return safe or "_"


def baseline_path(scenario: str, name: str, store: Path | None = None) -> Path:
    return store_root(store) / scenario_directory(scenario) / f"{check_name(name)}.json"


def is_reference(argument: str) -> bool:
    return argument.startswith(REFERENCE_PREFIX)


def reference_name(argument: str) -> str:
    return check_name(argument[len(REFERENCE_PREFIX) :])


def save_baseline(
    run: RunResult,
    name: str,
    *,
    source: Path,
    store: Path | None = None,
    force: bool = False,
) -> Path:
    """Store ``run`` as the baseline ``name`` of its scenario and return the file's path.

    The run gets two labels: ``physim-gate.baseline.source`` (the path it was saved from) and
    ``physim-gate.baseline.source_sha256`` (the content hash of that file or directory).
    """
    path = baseline_path(run.scenario.id, name, store)
    if path.exists() and not force:
        raise StoreError(f"{path} already exists (use --force to replace the baseline)")
    labels = {
        **run.labels,
        "physim-gate.baseline.source": str(source),
        "physim-gate.baseline.source_sha256": sha256_path(source),
    }
    try:
        write_run(run.model_copy(update={"labels": labels}), path)
    except OSError as exc:
        raise StoreError(f"{path}: cannot write ({exc.strerror or exc})") from exc
    return path


def load_baseline(scenario: str, name: str, store: Path | None = None) -> tuple[Path, RunResult]:
    """Load the stored baseline ``name`` for ``scenario``."""
    path = baseline_path(scenario, name, store)
    if not path.is_file():
        available = [b.name for b in list_baselines(store) if b.scenario == scenario]
        hint = f"; stored for this scenario: {', '.join(available)}" if available else ""
        raise StoreError(
            f"no baseline {name!r} for scenario {scenario!r} in {store_root(store)} "
            f"(save one with 'physim-gate baseline save'){hint}"
        )
    run = load_run(path)
    if run.scenario.id != scenario:
        raise StoreError(
            f"{path} holds scenario {run.scenario.id!r}, not {scenario!r}; two scenario ids map "
            "to the same directory, so store them separately with --store"
        )
    return path, run


@dataclass(frozen=True)
class StoredBaseline:
    scenario: str
    name: str
    path: Path
    run: RunResult | None
    error: str | None = None


def list_baselines(store: Path | None = None) -> list[StoredBaseline]:
    root = store_root(store)
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.glob("*/*.json")):
        try:
            run = load_run(path)
        except SchemaError as exc:
            found.append(StoredBaseline(path.parent.name, path.stem, path, None, exc.message))
            continue
        found.append(StoredBaseline(run.scenario.id, path.stem, path, run))
    return found
