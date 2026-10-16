# LeRobot example: gating a policy change on PushT

The three `eval_info.json` files in this directory are real, unedited output of LeRobot's `lerobot-eval` on the PushT simulator.
Each run has 100 episodes.
They were produced on 2026-10-17 on an Apple-silicon Mac (CPU only) with LeRobot 0.6.2 (commit `146d847`), using the community checkpoint [`caroo21/act_pusht`](https://huggingface.co/caroo21/act_pusht), an ACT policy trained on PushT keypoints.
The checkpoint is not very good: it succeeds in about 11% of episodes.
That is fine for this example, which is about the gate and not about the policy.

| Directory | What changed |
|---|---|
| `baseline/` | The checkpoint as published (`n_action_steps=32`) |
| `baseline-rerun/` | The same evaluation run again |
| `candidate-n-action-steps-8/` | The policy re-plans every 8 steps instead of every 32 (`--policy.n_action_steps=8`) |

The videos that `lerobot-eval` records are not included, so the `video_paths` entries point to files that do not exist here.

## Gate the change

```bash
physim-gate compare --adapter lerobot -O start_seed=1000 \
  -b examples/lerobot-pusht/baseline \
  -c examples/lerobot-pusht/candidate-n-action-steps-8 \
  -g examples/lerobot-pusht/gate.yaml
echo $?   # 1: regression detected
```

| Metric | Baseline | Candidate | Verdict | Why |
|---|--:|--:|---|---|
| `success` | 0.11 | 0.01 | INCONCLUSIVE | The drop is 10 points, but with 100 episodes the 95% interval is +3.4 to +17.7 points and includes the 5-point margin |
| `max_reward` | 0.711 | 0.554 | FAIL | The whole interval (+0.065 to +0.252) lies above the 0.05 margin |
| `sum_reward` (optional) | 82.8 | 110.3 | PASS | It improves, while the other two metrics got worse |

The gate fails because of `max_reward`.
The `success` result shows why the gate does not round a likely regression up to a FAIL: 1 success in 100 episodes against 11 in 100 is strong evidence that success dropped, but not that it dropped by more than the 5 points the gate allows.
To settle that, run more episodes.
`physim-gate plan --rate 0.11 --margin 0.05 --true-degradation 0.10` estimates how many: about 300 per run, if the true drop is 10 points.

`sum_reward` adds up the per-step reward over the whole episode, and an episode ends early only when it succeeds.
In this pair of runs it moved in the opposite direction from task completion, so it is optional here: it is reported, but it cannot block.
Which metrics deserve to block is a decision about your task, not something the gate can infer.

## Compare two runs of the same policy

```bash
physim-gate compare --adapter lerobot -O start_seed=1000 \
  -b examples/lerobot-pusht/baseline \
  -c examples/lerobot-pusht/baseline-rerun \
  -g examples/lerobot-pusht/gate.yaml
echo $?   # 0: no regression
```

Both runs used `--seed=1000`, so episode `i` was seeded with `1000 + i` in both and the adapter pairs them.
The rerun reproduced every success flag exactly.
The rewards differ only in floating-point rounding (at most 6e-14), so every interval is essentially zero-width and all three metrics pass.
That is real evidence that this setup is deterministic on one machine.
It is not evidence about other machines, GPUs or LeRobot versions.

## Reproduce the runs

```bash
pip install "lerobot[pusht]"
lerobot-eval --policy.path=caroo21/act_pusht --env.type=pusht \
  --env.obs_type=environment_state_agent_pos \
  --eval.n_episodes=100 --eval.batch_size=10 --eval.use_async_envs=false \
  --policy.device=cpu --seed=1000 --output_dir=baseline
# Candidate: add --policy.n_action_steps=8 and use another --output_dir.
```

`--env.obs_type=environment_state_agent_pos` is needed because this checkpoint was trained on keypoints, not images.
`--eval.use_async_envs=false` avoided a crash of the asynchronous vector environment on macOS.
Each run took about 15 seconds.
