# physim-gate-csv: an example third-party adapter

This package shows that a simulator integration can live outside PhySim Gate.
It registers one entry point and implements one method.

```toml
[project.entry-points."physim_gate.adapters"]
csv = "physim_gate_csv:CsvAdapter"
```

Try it from the repository root:

```bash
pip install -e examples/csv-adapter
physim-gate adapters            # lists "csv" with its distribution name
physim-gate compare \
  --adapter csv \
  -O directions=success:higher,time_to_goal:lower -O units=time_to_goal:s \
  -O scenario=warehouse_nav \
  -b examples/csv-adapter/data/baseline.csv \
  -c examples/csv-adapter/data/candidate.csv \
  -g examples/csv-adapter/data/gate.yaml
```

The candidate is slower per episode (about 0.33 s on average, out of about 20 s).
That is within the 5% relative margin, so the gate passes.

The data is synthetic.
