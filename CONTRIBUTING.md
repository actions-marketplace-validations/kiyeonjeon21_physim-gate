# Contributing to PhySim Gate

Thanks for helping.
PhySim Gate aims to stay small, dependable and easy to reason about, so changes are judged on correctness, clarity and test coverage more than on feature count.

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
# optional integrations
python -m pip install -e ".[gymnasium]" -e examples/csv-adapter
```

No GPU, simulator or container is needed.

## Checks

Run these before opening a pull request.
CI runs the same commands on Linux and macOS with Python 3.11 to 3.13.

```bash
ruff check .
ruff format --check .
mypy
python -m pytest
physim-gate demo --output-dir /tmp/physim-gate-demo
python -m build
```

Tests for optional integrations skip automatically when the integration is not installed.

## Ground rules

- **Statistical changes need a written rationale.** Update [docs/comparison-semantics.md](docs/comparison-semantics.md) in the same pull request, and add tests that pin the new behavior, including edge cases (zero baselines, missing values, tiny samples).
- **Do not claim support you have not tested.** A new adapter needs fixtures and tests that run in CI without the simulator installed.
- **Schema changes are versioned.** Additive, optional fields bump the minor version. Anything else bumps the major version and needs a migration note. Regenerate `schemas/*.json` with `physim-gate schema run|comparison|config`; a test fails if any of the three drifts.
- **Fixtures are generated.** Edit the YAML specs under `src/physim_gate/fixtures/` and regenerate `examples/highway-merge/*.json` (see `tests/test_examples.py`). Do not hand-edit generated JSON.
- **No proprietary data.** Use synthetic data, or output you produced yourself from public simulators and checkpoints, with a README that says exactly how to reproduce it (see `examples/lerobot-pusht/`). Do not add proprietary datasets, logs or code.
- **Keep dependencies small.** New runtime dependencies need a strong reason.

## Commit messages and the changelog

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `ci:`, `chore:`).
`CHANGELOG.md` is generated from the commit history with [git-cliff](https://git-cliff.org/) using `cliff.toml`, and is never edited by hand:

```bash
git cliff --tag v0.2.0 -o CHANGELOG.md
```

## Releasing

1. Bump the version in `pyproject.toml` and `src/physim_gate/__init__.py`, and the action tag in the README and `examples/github-actions/physim-gate.yml`.
   If the adapter contract still holds, widen the `physim-gate` upper bound in `examples/csv-adapter/pyproject.toml`; the `extras` CI job fails otherwise.
2. Generate the changelog with git-cliff (above) and commit it.
3. Tag the commit `vX.Y.Z`, push the tag, and publish a GitHub release for it.
4. Publishing the release runs `.github/workflows/release.yml`.
   It first runs the whole CI workflow on the released commit, then builds, checks and tests the distributions and uploads them to PyPI with [trusted publishing](https://docs.pypi.org/trusted-publishers/).
   No API token is involved, and nothing is uploaded unless every CI job passes.
   The `pypi` environment requires a maintainer's approval: approve the waiting deployment on the workflow run page.

Trusted publishing has to be set up once on pypi.org: add a publisher for owner `kiyeonjeon21`, repository `physim-gate`, workflow `release.yml` and environment `pypi`.
Running the workflow manually publishes to TestPyPI instead, which needs the same setup on test.pypi.org with environment `testpypi`.

## Reporting issues

Use the issue templates.
For wrong verdicts, attach the `comparison.json` (it contains everything needed to reproduce the decision) or a minimal pair of run results and the gate config, using synthetic data if the originals are confidential.

## License

By contributing you agree that your contributions are licensed under the Apache License 2.0.
