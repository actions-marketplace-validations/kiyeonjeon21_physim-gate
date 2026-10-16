"""Reads the ``eval_info.json`` that LeRobot's ``lerobot-eval`` writes to its output directory.

Two layouts exist, and LeRobot's own CI parser (``scripts/ci/parse_eval_metrics.py``) reads both:

* multi-task, written by LeRobot 0.4.0 and later: ``per_task`` (one entry per ``task_group`` and
  ``task_id`` with per-episode lists ``sum_rewards``, ``max_rewards`` and ``successes``), plus
  ``per_group`` and ``overall`` aggregates;
* single-task, written by LeRobot 0.3.x and earlier: ``per_episode`` (records with ``episode_ix``,
  ``sum_reward``, ``max_reward``, ``success`` and ``seed``), plus ``aggregated``.

Only the per-episode values are read. The aggregates are recomputed by the comparison, so they
are ignored, including the ``NaN`` values ``json.dump`` writes for empty groups.

Each episode becomes one trial with metrics ``success`` (0/1), ``sum_reward`` and ``max_reward``.
The trial id is the episode's position (``<task_group>/<task_id>/<index>`` or ``episode-<ix>``).
``lerobot-eval`` seeds episode ``i`` of every task with ``--seed + i``, so two evaluations run
with the same ``--seed`` and episode count share trial ids and can be compared as pairs. The
multi-task layout does not record seeds; pass ``-O start_seed=N`` to record them.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from physim_gate.adapters.base import AdapterError, reject_unknown_options
from physim_gate.schema import (
    Direction,
    Domain,
    Identifier,
    InputError,
    MetricSpec,
    RunResult,
    SchemaError,
    Simulator,
    Trial,
    format_validation_error,
    reject_duplicate_keys,
)

EVAL_INFO = "eval_info.json"

METRICS = {
    "success": MetricSpec(
        direction=Direction.HIGHER_IS_BETTER,
        description="1 if the episode succeeded, else 0 (LeRobot 'success').",
    ),
    "sum_reward": MetricSpec(
        direction=Direction.HIGHER_IS_BETTER,
        description="Sum of rewards over the episode (LeRobot 'sum_reward').",
    ),
    "max_reward": MetricSpec(
        direction=Direction.HIGHER_IS_BETTER,
        description="Maximum reward reached in the episode (LeRobot 'max_reward').",
    ),
}

#: Keys of a ``per_task`` entry's ``metrics`` mapped to run-result metric names.
_TASK_LISTS = {"successes": "success", "sum_rewards": "sum_reward", "max_rewards": "max_reward"}

OPTIONS = {"run_id", "policy", "scenario", "start_seed", "task_group"}


class LeRobotAdapter:
    name = "lerobot"
    description = "LeRobot lerobot-eval output (eval_info.json, or its output directory)."

    def load(self, source: Path, options: Mapping[str, str]) -> RunResult:
        reject_unknown_options(self.name, options, OPTIONS)
        path = source / EVAL_INFO if source.is_dir() else source
        data = _read(path)
        start_seed = _int_option(options, "start_seed")
        group_filter = options.get("task_group")

        if "per_task" in data:
            trials, groups = _multi_task(data["per_task"], path, start_seed, group_filter)
        elif "per_episode" in data:
            if group_filter is not None:
                raise AdapterError(
                    f"{path}: option task_group needs the multi-task layout ('per_task'), "
                    "but this file has 'per_episode'"
                )
            trials, groups = _single_task(data["per_episode"], path, start_seed), []
        else:
            raise SchemaError(
                f"{path}: not a lerobot-eval result; expected a 'per_task' or 'per_episode' key, "
                f"found {', '.join(sorted(data)) or 'none'}"
            )
        if not trials:
            raise SchemaError(f"{path}: contains no episodes")

        default_scenario = "+".join(groups) if groups else "lerobot-eval"
        try:
            return RunResult(
                run_id=options.get("run_id") or _default_run_id(source, path),
                domain=Domain.ROBOTICS,
                simulator=Simulator(name="lerobot"),
                scenario=Identifier(id=options.get("scenario") or default_scenario),
                policy=Identifier(id=options["policy"]) if options.get("policy") else None,
                seeds=sorted({t.seed for t in trials if t.seed is not None}),
                metrics=dict(METRICS),
                trials=trials,
                labels={"lerobot.layout": "per_task" if groups else "per_episode"},
            )
        except ValidationError as exc:
            raise SchemaError(
                f"{path}: cannot convert to a run result", format_validation_error(exc)
            ) from exc


def _read(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"{path}: cannot read file ({exc.strerror or exc})") from exc
    try:
        # lerobot-eval writes aggregates with json.dump, which emits NaN for empty groups.
        # Python's json module accepts that token, so per-episode NaN values are mapped to
        # missing values below instead of rejecting the whole file.
        data = json.loads(text, object_pairs_hook=reject_duplicate_keys(str(path)))
    except json.JSONDecodeError as exc:
        raise SchemaError(f"{path}: invalid JSON at line {exc.lineno}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise SchemaError(f"{path}: expected a JSON object at the top level")
    return data


def _int_option(options: Mapping[str, str], key: str) -> int | None:
    raw = options.get(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise AdapterError(f"adapter option {key}={raw!r} must be an integer") from None


def _value(raw: Any, where: str) -> float | None:
    """A per-episode number, or ``None`` for a missing value (JSON null or NaN)."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    if not isinstance(raw, int | float):
        raise SchemaError(f"{where}: expected a number or boolean, got {type(raw).__name__}")
    number = float(raw)
    if math.isnan(number):
        return None
    if math.isinf(number):
        raise SchemaError(f"{where}: infinite value")
    return number


def _multi_task(
    entries: Any, path: Path, start_seed: int | None, group_filter: str | None
) -> tuple[list[Trial], list[str]]:
    if not isinstance(entries, list):
        raise SchemaError(f"{path}: 'per_task' must be a list")
    trials: list[Trial] = []
    groups: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        where = f"{path}: per_task[{index}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("metrics"), dict):
            raise SchemaError(
                f"{where}: expected an object with 'task_group', 'task_id', 'metrics'"
            )
        group, task = str(entry.get("task_group", "")), str(entry.get("task_id", ""))
        if not group or not task:
            raise SchemaError(f"{where}: missing 'task_group' or 'task_id'")
        if (group, task) in seen:
            raise SchemaError(f"{where}: duplicate task {group}/{task}")
        seen.add((group, task))
        if group_filter is not None and group != group_filter:
            continue
        if group not in groups:
            groups.append(group)

        lists: dict[str, list[Any]] = {}
        for key in _TASK_LISTS:
            values = entry["metrics"].get(key)
            if not isinstance(values, list):
                raise SchemaError(f"{where}.metrics.{key}: expected a list of per-episode values")
            lists[key] = values
        lengths = {key: len(values) for key, values in lists.items()}
        if len(set(lengths.values())) != 1:
            detail = ", ".join(f"{k}={n}" for k, n in lengths.items())
            raise SchemaError(f"{where}: per-episode lists differ in length ({detail})")

        for episode in range(lengths["successes"]):
            trials.append(
                Trial(
                    trial_id=f"{group}/{task}/{episode}",
                    seed=None if start_seed is None else start_seed + episode,
                    metrics={
                        name: _value(lists[key][episode], f"{where}.metrics.{key}[{episode}]")
                        for key, name in _TASK_LISTS.items()
                    },
                    labels={"task_group": group, "task_id": task},
                )
            )
    if group_filter is not None and not groups:
        found = sorted({g for g, _ in seen})
        raise AdapterError(
            f"{path}: no task group {group_filter!r}; found {', '.join(found) or 'none'}"
        )
    return trials, groups


def _single_task(entries: Any, path: Path, start_seed: int | None) -> list[Trial]:
    if not isinstance(entries, list):
        raise SchemaError(f"{path}: 'per_episode' must be a list")
    trials: list[Trial] = []
    for index, entry in enumerate(entries):
        where = f"{path}: per_episode[{index}]"
        if not isinstance(entry, dict):
            raise SchemaError(f"{where}: expected an object")
        episode = entry.get("episode_ix", index)
        seed = entry.get("seed")
        if seed is None and start_seed is not None:
            seed = start_seed + index
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise SchemaError(f"{where}.seed: expected an integer or null")
        trials.append(
            Trial(
                trial_id=f"episode-{episode}",
                seed=seed,
                metrics={name: _value(entry.get(name), f"{where}.{name}") for name in METRICS},
            )
        )
    return trials


def _default_run_id(source: Path, path: Path) -> str:
    """The output directory's name, which lerobot-eval makes unique per evaluation."""
    directory = source if source.is_dir() else path.parent
    return directory.resolve().name or path.stem
