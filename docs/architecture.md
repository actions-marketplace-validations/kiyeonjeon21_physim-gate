# Architecture

PhySim Gate is a single Python package with a short pipeline:

```text
              adapters                engine                   reporting
native output ─────────> RunResult ─┐
                                    ├─> compare_runs() ─> ComparisonResult ─┬─> terminal
native output ─────────> RunResult ─┘        ^                              ├─> JSON
                                             │                              ├─> JUnit XML
                           gate.yaml ─> GateConfig                          └─> HTML
```

Every arrow is a typed, validated, versioned object.
Each stage can be used alone from Python, and the CLI is a thin layer over them.

## Modules

| Module | Responsibility |
|---|---|
| `schema.py` | The run-result models (`RunResult`, `Trial`, `MetricSpec`, ...), loading, strict validation and deterministic JSON output |
| `validation.py` | Warnings for documents that are valid but suspicious (`physim-gate validate`) |
| `config.py` | The gate config models and defaults resolution |
| `stats.py` | Dependency-free point estimates, quantiles and the paired/unpaired bootstrap |
| `engine.py` | `compare_runs()`: run-level checks, per-metric numerical, evidence and policy layers |
| `comparison.py` | The versioned `ComparisonResult` models, verdict vocabulary and exit codes |
| `provenance.py` | Environment capture, git provenance, file hashes, `SOURCE_DATE_EPOCH` |
| `adapters/` | The adapter protocol, entry-point discovery, and the `json`, `synthetic` and `gymnasium` adapters |
| `reporting/` | Terminal (rich), JUnit XML and self-contained HTML renderers |
| `demo.py`, `fixtures/` | The bundled scenarios and the `demo` command |
| `cli.py` | The Typer CLI, error handling and exit codes |

## Design decisions

**Evaluate artifacts, never run simulators.**
Running simulators couples a tool to their installation, licensing, GPUs and failure modes.
Reading result files keeps PhySim Gate installable anywhere in seconds, and lets it sit behind any existing simulation job.
The cost is that users must produce result files; adapters and the JSON Schema make that cheap.

**Three layers per metric.**
The numerical estimate, the statistical evidence and the policy verdict are stored separately.
A reader can see that a metric "degraded by 0.3 against a margin of 0.5, but the interval straddles the margin, and the policy treats that as blocking".
Collapsing these into a single pass/fail hides exactly the information needed to act.

**Conservative defaults.**
A metric passes only when non-inferiority is shown.
Missing values and inconclusive evidence block unless configured otherwise.
Each default can be relaxed explicitly in `gate.yaml`, and reports always show what was relaxed.

**Pure-Python statistics.**
Sample sizes in simulation regression testing are usually tens to thousands of trials.
A stdlib bootstrap handles that comfortably: on an Apple Silicon laptop, 10,000 resamples took about 0.1 s per metric for 100 paired trials and about 1 s for 1,000 trials.
It keeps the runtime dependencies to pydantic, typer and PyYAML, and produces identical numbers on every platform.
NumPy or SciPy would be justified for much larger samples or more advanced intervals; see the roadmap.

**Self-contained results.**
`ComparisonResult` embeds the gate config, both runs' provenance, the analyzed values and input file hashes.
`physim-gate report` can regenerate every output from it, and the comparison can be audited later without the original files.

**Deterministic output.**
Stable field order, seeded bootstrap and `SOURCE_DATE_EPOCH` make outputs byte-reproducible, so they can be committed, diffed and cached.

**Strict schemas, open extension points.**
Unknown fields are errors, which catches typos like `diretion`.
Free-form data has explicit homes (`extra`, `labels`, `environment.extra`).

**Plugins through entry points.**
Python packaging already solves discovery.
An adapter is one class registered under `physim_gate.adapters`; no registry service or core change is involved.
A broken plugin is reported by `physim-gate adapters` and does not affect other adapters.

## Error model

| Situation | Exception | CLI exit code |
|---|---|---|
| Unreadable file, invalid JSON/YAML, schema violation | `SchemaError` (with one line per problem) | 2 |
| Unknown or broken adapter, bad adapter option | `AdapterError` | 2 |
| Valid inputs that cannot be compared (metric missing, unit mismatch, ...) | none; the metric is `INVALID` | 3 (if blocking) |
| Anything else | unexpected exception, traceback printed | 4 |

## MVP acceptance criteria (v0.1.0)

- `pip install -e ".[dev]"` followed by `physim-gate demo` works on Linux and macOS without a GPU or simulator, and produces PASS, FAIL, INCONCLUSIVE and INVALID scenarios through the real engine.
- `validate`, `compare`, `report`, `init`, `adapters`, `schema` and `demo` exist, have `--help`, and use the documented exit codes.
- Comparisons support absolute, relative and combined margins, both directions, zero baselines, paired and unpaired samples, missing data and insufficient samples, each with an explicit outcome.
- JSON, JUnit XML and self-contained HTML outputs are produced and covered by tests.
- A third-party adapter can be installed and used without modifying the package.
- Tests, Ruff, mypy (strict) and the package build pass.
