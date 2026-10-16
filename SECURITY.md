# Security policy

## Supported versions

Security fixes go into the latest release only.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's [private vulnerability reporting](https://github.com/kiyeonjeon21/physim-gate/security/advisories/new), not in a public issue.
Include the version, the command you ran and, if possible, a minimal input that shows the problem.
You can expect a first reply within a week.

## What counts

PhySim Gate reads result files and configs and writes reports.
Problems in scope include, for example, a crafted result file that makes the HTML or Markdown report execute script, or that makes PhySim Gate write outside the paths it was given.

Adapters are ordinary Python code, and the `gymnasium` adapter imports and calls the policy function its spec names.
Running an untrusted adapter or spec is equivalent to running untrusted code, and that alone is not a vulnerability in PhySim Gate.
