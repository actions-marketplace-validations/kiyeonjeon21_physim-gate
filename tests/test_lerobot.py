from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from physim_gate.adapters.base import AdapterError, get_adapter
from physim_gate.schema import Direction, Domain, SchemaError, dump_json, run_from_data

DATA = Path(__file__).parent / "data" / "lerobot"
#: Written by ``lerobot-eval`` (LeRobot 0.6.2, PushT, policy caroo21/act_pusht, --seed=1000).
REAL = DATA / "pusht_act_4_episodes.json"


def load(source: Path, **options: str) -> Any:
    return get_adapter("lerobot").load(source, options)


def write(tmp_path: Path, data: Any, text: str | None = None) -> Path:
    path = tmp_path / "eval_info.json"
    path.write_text(text if text is not None else json.dumps(data))
    return path


def task(group: str, task_id: int, successes: list[Any], rewards: list[Any] | None = None) -> Any:
    rewards = rewards if rewards is not None else [1.0] * len(successes)
    return {
        "task_group": group,
        "task_id": task_id,
        "metrics": {
            "sum_rewards": rewards,
            "max_rewards": [1.0] * len(successes),
            "successes": successes,
            "video_paths": [],
            "predicted_video_paths": [],
        },
    }


def test_reads_real_lerobot_eval_output() -> None:
    run = load(REAL, policy="caroo21/act_pusht")
    assert run.simulator.name == "lerobot"
    assert run.domain is Domain.ROBOTICS
    assert run.scenario.id == "pusht"
    assert run.policy is not None
    assert run.policy.id == "caroo21/act_pusht"
    assert [t.trial_id for t in run.trials] == [f"pusht/0/{i}" for i in range(4)]
    assert [t.metrics["success"] for t in run.trials] == [0.0, 1.0, 0.0, 0.0]
    assert run.trials[1].metrics["max_reward"] == 1.0
    assert run.trials[0].labels == {"task_group": "pusht", "task_id": "0"}
    assert set(run.metrics) == {"success", "sum_reward", "max_reward"}
    assert all(spec.direction is Direction.HIGHER_IS_BETTER for spec in run.metrics.values())
    assert run.seeds == []
    # The converted run is a valid, serializable run result.
    assert run_from_data(json.loads(dump_json(run))) == run


def test_reads_an_output_directory(tmp_path: Path) -> None:
    out = tmp_path / "eval-2026-10-17"
    out.mkdir()
    (out / "eval_info.json").write_text(REAL.read_text())
    run = load(out)
    assert run.run_id == "eval-2026-10-17"
    assert len(run.trials) == 4


def test_start_seed_records_lerobot_seeding() -> None:
    run = load(REAL, start_seed="1000", run_id="base")
    assert run.run_id == "base"
    assert [t.seed for t in run.trials] == [1000, 1001, 1002, 1003]
    assert run.seeds == [1000, 1001, 1002, 1003]


def test_multi_task_groups_and_filter(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        {
            "per_task": [
                task("libero_spatial", 0, [True, False]),
                task("libero_spatial", 1, [True, True]),
                task("libero_object", 0, [False, False]),
            ],
            "per_group": {},
            "overall": {},
        },
    )
    run = load(path)
    assert run.scenario.id == "libero_spatial+libero_object"
    assert [t.trial_id for t in run.trials][:3] == [
        "libero_spatial/0/0",
        "libero_spatial/0/1",
        "libero_spatial/1/0",
    ]
    only = load(path, task_group="libero_object", scenario="objects")
    assert only.scenario.id == "objects"
    assert [t.trial_id for t in only.trials] == ["libero_object/0/0", "libero_object/0/1"]
    with pytest.raises(AdapterError, match="no task group 'nope'; found libero_object"):
        load(path, task_group="nope")


def test_nan_aggregates_are_ignored_and_nan_episodes_are_missing(tmp_path: Path) -> None:
    text = json.dumps(
        {"per_task": [task("g", 0, [True, False], rewards=[1.5, None])], "overall": {}}
    ).replace('"overall": {}', '"overall": {"pc_success": NaN}')
    text = text.replace("null", "NaN")
    run = load(write(tmp_path, None, text))
    assert run.trials[1].metrics["sum_reward"] is None
    assert run.trials[0].metrics["sum_reward"] == 1.5


def test_legacy_single_task_layout(tmp_path: Path) -> None:
    # The layout LeRobot 0.3.x wrote (eval_policy()'s result, saved directly).
    path = write(
        tmp_path,
        {
            "per_episode": [
                {
                    "episode_ix": 0,
                    "sum_reward": 10.0,
                    "max_reward": 1.0,
                    "success": True,
                    "seed": 7,
                },
                {
                    "episode_ix": 1,
                    "sum_reward": 4.0,
                    "max_reward": 0.5,
                    "success": False,
                    "seed": 8,
                },
            ],
            "aggregated": {"pc_success": 50.0},
        },
    )
    run = load(path, scenario="pusht")
    assert [t.trial_id for t in run.trials] == ["episode-0", "episode-1"]
    assert [t.seed for t in run.trials] == [7, 8]
    assert run.scenario.id == "pusht"
    assert run.labels == {"lerobot.layout": "per_episode"}
    with pytest.raises(AdapterError, match="task_group needs the multi-task layout"):
        load(path, task_group="x")
    with pytest.raises(AdapterError, match="-O scenario=<name>"):
        load(path)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"overall": {}}, "expected a 'per_task' or 'per_episode' key, found overall"),
        ({"per_task": {}}, "'per_task' must be a list"),
        ({"per_task": []}, "contains no episodes"),
        ({"per_task": [{"task_group": "g"}]}, r"per_task\[0\]: expected an object"),
        ({"per_task": [{"metrics": {}}]}, "missing 'task_group' or 'task_id'"),
        ({"per_task": [task("g", 0, [True]), task("g", 0, [True])]}, "duplicate task g/0"),
        (
            {"per_task": [task("g", 0, [True, False], rewards=[1.0])]},
            r"lists differ in length \(successes=2, sum_rewards=1, max_rewards=2\)",
        ),
        ({"per_task": [task("g", 0, ["yes"])]}, "expected a number or boolean, got str"),
        ({"per_episode": [{"seed": "1"}]}, "seed: expected an integer or null"),
        ({"per_episode": {}}, "'per_episode' must be a list"),
        ({"per_episode": [1]}, r"per_episode\[0\]: expected an object"),
        (
            {"per_episode": [{"episode_ix": 0}, {"episode_ix": 0}]},
            r"cannot convert to a run result\n  - <root>: trials\[1\]: duplicate trial_id",
        ),
        ({"per_task": [{**task("g", 0, [True]), "metrics": {}}]}, "expected a list of per-episode"),
        ([], "expected a JSON object"),
    ],
)
def test_rejects_malformed_files(tmp_path: Path, data: Any, message: str) -> None:
    with pytest.raises(SchemaError, match=message):
        load(write(tmp_path, data))


def test_infinite_values_and_seed_numbering_for_the_single_task_layout(tmp_path: Path) -> None:
    path = write(tmp_path, None, '{"per_task": [' + json.dumps(task("g", 0, [True])) + "]}")
    path.write_text(path.read_text().replace("[1.0]", "[Infinity]", 1))
    with pytest.raises(SchemaError, match="infinite value"):
        load(path)
    path = write(tmp_path, {"per_episode": [{"success": 1}, {"success": 0}]})
    assert [t.seed for t in load(path, start_seed="5", scenario="s").trials] == [5, 6]
    assert [t.seed for t in load(path, scenario="s").trials] == [None, None]


def test_rejects_unreadable_input_and_bad_options(tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match="cannot read file"):
        load(tmp_path)
    with pytest.raises(SchemaError, match="invalid JSON"):
        load(write(tmp_path, None, "{"))
    with pytest.raises(AdapterError, match="must be an integer"):
        load(REAL, start_seed="x")
    with pytest.raises(AdapterError, match="does not accept option"):
        load(REAL, seed="1")


def test_single_task_layout_needs_a_scenario_name(tmp_path: Path) -> None:
    """The 0.3.x layout does not say which environment it evaluated.

    Without a name, a PushT baseline and an ALOHA candidate would both be scenario
    'lerobot-eval' and pass the same-scenario check, and would share one stored baseline.
    """
    from typer.testing import CliRunner

    from physim_gate.cli import app

    episodes = [{"episode_ix": i, "success": i % 3 != 0, "sum_reward": 1.0, "max_reward": 1.0}
                for i in range(40)]  # fmt: skip
    pusht, aloha = tmp_path / "pusht", tmp_path / "aloha"
    for directory in (pusht, aloha):
        directory.mkdir()
        write(directory, {"per_episode": episodes})
    gate = tmp_path / "gate.yaml"
    gate.write_text("version: 1\nmetrics:\n  success:\n    max_absolute_degradation: 0.5\n")
    args = ["compare", "-a", "lerobot", "-b", str(pusht), "-c", str(aloha), "-g", str(gate)]

    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2, result.output
    assert "-O scenario=<name>" in result.output
    assert CliRunner().invoke(app, [*args, "-O", "scenario=pusht"]).exit_code == 0
