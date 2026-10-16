"""Semantic checks on top of schema validation.

Schema validation (:mod:`physim_gate.schema`) rejects documents that cannot be interpreted.
The checks here flag documents that are valid but probably not what the author intended,
for example a declared metric that no trial reports.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, ConfigDict

from physim_gate.schema import RunResult, RunStatus, TrialStatus


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    location: str
    message: str


def lint_run(run: RunResult) -> list[Issue]:
    """Return warnings for a schema-valid run. An empty list means nothing suspicious."""
    issues: list[Issue] = []

    if not run.trials:
        issues.append(Issue(location="trials", message="run contains no trials"))

    reported: Counter[str] = Counter()
    nulls: Counter[str] = Counter()
    for trial in run.trials:
        for name, value in trial.metrics.items():
            if value is None:
                nulls[name] += 1
            else:
                reported[name] += 1

    for name in run.metrics:
        missing = len(run.trials) - reported[name]
        if run.trials and reported[name] == 0:
            issues.append(
                Issue(location=f"metrics.{name}", message="declared but no trial reports a value")
            )
        elif missing:
            detail = f"{nulls[name]} null" if nulls[name] else ""
            absent = missing - nulls[name]
            if absent:
                detail = f"{detail}, {absent} absent" if detail else f"{absent} absent"
            issues.append(
                Issue(
                    location=f"metrics.{name}",
                    message=f"missing in {missing} of {len(run.trials)} trials ({detail})",
                )
            )

    for index, trial in enumerate(run.trials):
        if trial.status is not TrialStatus.OK and trial.failure is None:
            issues.append(
                Issue(
                    location=f"trials[{index}]",
                    message=f"trial {trial.trial_id!r} has status {trial.status.value!r} "
                    "but no failure details",
                )
            )
        if trial.status is TrialStatus.OK and trial.failure is not None:
            issues.append(
                Issue(
                    location=f"trials[{index}]",
                    message=f"trial {trial.trial_id!r} has status 'ok' but carries failure details",
                )
            )

    not_ok = sum(1 for t in run.trials if t.status is not TrialStatus.OK)
    if run.status is RunStatus.COMPLETED and run.trials and not_ok == len(run.trials):
        issues.append(Issue(location="status", message="run is 'completed' but every trial failed"))
    if run.status is RunStatus.FAILED and run.failure is None:
        issues.append(Issue(location="status", message="run status is 'failed' without details"))

    trial_seeds = {t.seed for t in run.trials if t.seed is not None}
    if run.seeds and trial_seeds and not trial_seeds <= set(run.seeds):
        issues.append(
            Issue(location="seeds", message="some trial seeds are not listed in run-level 'seeds'")
        )
    if not run.source.commit:
        issues.append(
            Issue(
                location="source.commit", message="no source commit; the run is hard to reproduce"
            )
        )
    if not run.simulator.version:
        issues.append(Issue(location="simulator.version", message="simulator version not recorded"))

    return issues
