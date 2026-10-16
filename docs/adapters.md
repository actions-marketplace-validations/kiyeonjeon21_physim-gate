# Adapters

An adapter converts a simulator's native output into a [run result](result-schema.md).
Adapters are the only simulator-specific code in the PhySim Gate ecosystem, and they can live in any package.

## The contract

```python
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from physim_gate.schema import RunResult


class Adapter(Protocol):
    name: str
    description: str

    def load(self, source: Path, options: Mapping[str, str]) -> RunResult: ...
```

- `source` is the path the user passed as `--baseline` or `--candidate`. It can be a file or a directory.
- `options` are the `-O key=value` pairs from the command line. Reject unknown keys so typos surface.
- Return a `RunResult`. The easiest way is `physim_gate.schema.run_from_data(dict, origin)`, which validates and produces readable errors.
- Raise `physim_gate.adapters.AdapterError` or `physim_gate.schema.SchemaError` for problems the user can fix. The CLI prints the message and exits with code 2.

## Registration

Register a zero-argument factory (usually the class) under the `physim_gate.adapters` entry-point group:

```toml
[project.entry-points."physim_gate.adapters"]
mysim = "my_package.adapter:MySimAdapter"
```

After installation:

```bash
physim-gate adapters               # shows "mysim" and the distribution it came from
physim-gate validate --adapter mysim run_dir/
physim-gate compare --adapter mysim -O split=val -b base_dir/ -c cand_dir/ -g gate.yaml
```

Discovery rules:

- Built-in adapters (`json`, `synthetic`, `gymnasium`, `lerobot`) are always available.
- A third-party adapter whose name collides with a different registration is listed as a conflict and ignored.
- An adapter that fails to import or does not satisfy the protocol is reported when selected, with the underlying error. Other adapters keep working.

## Guidelines

- **Map trial identity carefully.** `trial_id` should name the *condition* (scenario variant, seed, initial state), so that the same condition gets the same id in baseline and candidate runs. That enables paired comparison.
- **Keep failed trials.** Emit them with `status` and `failure`, and report metric values where they are meaningful (for example `success: 0`). Do not drop them.
- **Use `null` for unavailable values**, never `0` or a sentinel such as `-1`.
- **Declare direction and unit** for every metric.
- **Record provenance.** Simulator version, seeds and source commit make comparisons reproducible. `physim_gate.provenance.capture_environment()` and `capture_git_source()` help.
- **Stay light.** Import heavy simulator libraries inside `load`, not at module import, so `physim-gate adapters` stays fast.

## Built-in adapters

| Name | Input | Notes |
|---|---|---|
| `json` | A PhySim Gate run-result JSON file | The default |
| `synthetic` | A YAML spec | Deterministic synthetic data for demos and tests. Options: `seed`, `trials` |
| `gymnasium` | A YAML spec with `env_id`, `policy`, `run_id`, `episodes`, ... | Requires the `gymnasium` extra. Rolls out `policy` (a `module:function` mapping observations to actions, or `random`) once per seed. Tested with CartPole on CPU |
| `lerobot` | LeRobot's `eval_info.json`, or the `lerobot-eval` output directory that contains it | No extra dependency. See below |

Security note: adapters are ordinary Python code, and the `gymnasium` adapter imports and calls the policy function named in its spec.
Treat adapter specs and installed plugins like any other code you run.

## LeRobot

`lerobot-eval` writes `eval_info.json` to its `--output_dir`.
The adapter reads both layouts that LeRobot's own CI parser (`scripts/ci/parse_eval_metrics.py` in the LeRobot repository) recognizes:

- **Multi-task**, written by LeRobot 0.4.0 and later: `per_task` entries with `task_group`, `task_id` and per-episode lists `sum_rewards`, `max_rewards` and `successes`.
- **Single-task**, written by LeRobot 0.3.x and earlier: `per_episode` records with `episode_ix`, `sum_reward`, `max_reward`, `success` and `seed`.

Each episode becomes one trial with the metrics `success` (0 or 1), `sum_reward` and `max_reward`, all higher-is-better.
The aggregates (`overall`, `per_group`, `aggregated`) are ignored, because the comparison recomputes everything from the episodes.
That also skips the `NaN` values `json.dump` writes there for empty groups; a `NaN` or `null` in an episode value becomes a missing value.

Trial ids are `<task_group>/<task_id>/<episode index>` (multi-task) or `episode-<episode_ix>` (single-task).
`lerobot-eval` seeds episode `i` of every task with `--seed + i`, so two evaluations with the same `--seed` and episode count share trial ids and are compared as matched pairs.
The multi-task layout does not record seeds, so the adapter cannot check that both evaluations used the same one; that is up to you.

| Option | Meaning |
|---|---|
| `start_seed` | The `--seed` given to `lerobot-eval`; records each trial's seed as `start_seed + i` |
| `task_group` | Read only this task group, for example `libero_spatial` |
| `run_id` | Run id; defaults to the name of the output directory |
| `policy` | Policy id to record, for example the Hugging Face repository of the checkpoint |
| `scenario` | Scenario id; defaults to the task groups joined with `+` |

Tested with real output of LeRobot 0.6.2 on PushT ([`examples/lerobot-pusht/`](../examples/lerobot-pusht)).
The multi-group and single-task layouts are tested with files constructed from LeRobot's source, not with real LIBERO or MetaWorld output.

## Example: CSV

[`examples/csv-adapter/`](../examples/csv-adapter) is a complete third-party adapter package that reads one CSV row per trial.
CI installs it and runs a comparison through it, which tests the plugin path end to end.

## Planned adapters

These are not implemented.
They are listed to show intended scope; see the [roadmap](roadmap.md).

- ROS 2 bag summaries (via user-defined metric extractors).
- PX4 SITL ULog flight logs.
- CARLA ScenarioRunner and Leaderboard result JSON.
