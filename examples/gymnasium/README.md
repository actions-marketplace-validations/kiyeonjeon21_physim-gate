# Gymnasium example: comparing two CartPole controllers

This example runs real rollouts on CPU with [Gymnasium](https://gymnasium.farama.org/) and gates the result.
The baseline controller uses the pole angle and angular velocity.
The candidate uses only the angle and balances for far fewer steps, so the gate reports a regression.

```bash
pip install -e ".[gymnasium]"
PYTHONPATH=examples/gymnasium physim-gate compare --adapter gymnasium \
  -b examples/gymnasium/baseline.yaml \
  -c examples/gymnasium/candidate.yaml \
  -g examples/gymnasium/gate.yaml
echo $?   # 1: regression detected
```

`PYTHONPATH` makes `policies.py` importable, because the specs refer to policies as `policies:<function>`.

Each spec runs 30 episodes with seeds 0 to 29 in both runs, so the trials are compared as matched pairs.
Seeding `reset` fixes the initial state; it does not by itself make every environment deterministic.

Note that loading a spec imports and runs the Python function named in `policy`.
Only use specs you trust.
