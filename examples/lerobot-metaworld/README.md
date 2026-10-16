# LeRobot example: a multi-task MetaWorld evaluation

The two `eval_info.json` files in this directory are real, unedited output of LeRobot's `lerobot-eval` on four MetaWorld tasks, 25 episodes per task and 100 per run.
They were produced on 2026-10-17 on an Apple-silicon Mac (MPS) with LeRobot 0.6.2 (commit `146d847`) and MetaWorld 3.0.0, using the checkpoint [`lerobot/smolvla_metaworld`](https://huggingface.co/lerobot/smolvla_metaworld), a SmolVLA policy.
They use LeRobot's multi-task layout (`per_task`), so each episode's trial id is `<task>/0/<episode>`.

| Directory | What changed |
|---|---|
| `baseline/` | The checkpoint as published (`n_action_steps=50`) |
| `candidate-n-action-steps-10/` | The policy re-plans every 10 steps instead of every 50 (`--policy.n_action_steps=10`) |

The videos that `lerobot-eval` records are not included.

## Gate the change

```bash
physim-gate compare --adapter lerobot -O start_seed=1000 -O scenario=metaworld \
  -b examples/lerobot-metaworld/baseline \
  -c examples/lerobot-metaworld/candidate-n-action-steps-10 \
  -g examples/lerobot-metaworld/gate.yaml
echo $?   # 0: no regression
```

| Task | Baseline successes | Candidate successes |
|---|--:|--:|
| `reach-v3` | 8 / 25 | 15 / 25 |
| `button-press-v3` | 22 / 25 | 25 / 25 |
| `drawer-open-v3` | 15 / 25 | 23 / 25 |
| `door-open-v3` | 23 / 25 | 21 / 25 |
| All | 68 / 100 | 84 / 100 |

Re-planning more often raised the overall success rate from 68% to 84%, and the 95% interval of the degradation (-25 to -7 points) lies well below the 5-point margin, so `success` passes.

The gate looks at all 100 episodes together.
It does not notice that one task, `door-open-v3`, went from 23 to 21 successes.
With 25 episodes per task, that difference is well within noise, but a gate on pooled results can in general hide a regression in one task behind gains in others.
Per-task verdicts are on the [roadmap](../../docs/roadmap.md); until then, use `-O task_group=door-open-v3` to gate one task, or list each task as a scenario in a suite.
Here that gives `INCONCLUSIVE` for every task: 25 episodes are fewer than this gate's `min_samples` of 30, and gating each task on its own needs more episodes per task than gating the pool.

`max_reward` is optional here: MetaWorld rewards are shaped per task, so their scale differs between tasks and the pooled mean mostly reflects which tasks are easy.

`-O scenario=metaworld` names the scenario.
Without it, the adapter joins the task groups (`reach-v3+button-press-v3+...`).

## Reproduce the runs

```bash
pip install "lerobot[metaworld,smolvla]"
lerobot-eval --policy.path=lerobot/smolvla_metaworld --env.type=metaworld \
  --env.task=reach-v3,button-press-v3,drawer-open-v3,door-open-v3 \
  --eval.n_episodes=25 --eval.batch_size=1 --eval.use_async_envs=false \
  --policy.device=mps --seed=1000 --output_dir=baseline \
  --rename_map='{"observation.image": "observation.images.camera1"}'
# Candidate: add --policy.n_action_steps=10 and use another --output_dir.
```

`--rename_map` maps the environment's camera to the name the checkpoint was trained with; without it `lerobot-eval` stops with a feature mismatch.
Each run took 6 to 10 minutes.
Unlike the PushT example, determinism was not checked by a second run.
