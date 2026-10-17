# Changelog

All notable changes to this project are documented here.
This file is generated from the commit history by git-cliff.

## [0.4.0] - 2026-10-17

### Bug fixes

- **stats:** Use Welch degrees of freedom in the expanded two-sample bootstrap
- **plan:** Plan underpowered pilots with the gate's method and confidence
- **action:** Never report a stale verdict, and do not fail on fork pull requests
- **lerobot:** Require a scenario name for the single-task layout
- **engine:** Block comparisons of runs that are not comparable
- **terminal:** Wrap long summaries and notes with a hanging indent

### Documentation

- Document the P1 fixes of the v0.3.0 review

### Build and CI

- Run the whole CI workflow before publishing a release

## [0.3.1] - 2026-10-17

### Bug fixes

- Close three ways to get a silently wrong verdict or exit code

### Documentation

- Add the v0.3.0 review with prioritized findings

## [0.3.0] - 2026-10-16

### Features

- **stats:** Use the expanded percentile bootstrap by default
- Paired and pilot-based planning, a baseline store and suites

### Bug fixes

- **report:** Keep Markdown table values on one line in narrow comments
- **terminal:** Never truncate the metric table
- **suite:** Never wrap the path in a scenario's error message

### Documentation

- Describe planning from a pilot, baselines, suites and the new bootstrap
- **examples:** Add real multi-task MetaWorld output and a suite example
- Regenerate report screenshots for the expanded percentile bootstrap

## [0.2.0] - 2026-10-16

### Features

- **adapters:** Add a LeRobot eval_info.json adapter
- **cli:** Add sample-size planning and Markdown summaries

### Bug fixes

- **cli:** Never wrap the paths of written reports
- **report:** Start the Markdown summary sentence with a capital letter
- **examples:** Allow physim-gate 0.2 in the CSV adapter example

### Documentation

- Describe the LeRobot example, planning and the GitHub Action

### Build and CI

- Add a GitHub Action, a PyPI release workflow and Dependabot

## [0.1.1] - 2026-10-16

### Bug fixes

- **stats:** Use Newcombe score intervals for 0/1 metrics
- **cli:** Keep exit codes correct for edge-case inputs

### Documentation

- Add OSS readiness review and correct overstated claims

### Build and CI

- Raise dependency floors to tested versions

## [0.1.0] - 2026-10-16

### Features

- Core schema, comparison engine, adapters, reports and CLI

### Bug fixes

- **report:** Tidy terminal and HTML output

### Documentation

- README, architecture, semantics, schema, adapters and roadmap

### Build and CI

- Move workflow actions to Node 24 releases

